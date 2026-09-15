import { Link } from "react-router-dom";
import { Capacitor } from "@capacitor/core";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import AppShell from "../AppShell";
import { ApiError, AuthExpiredError, sendConversation, synthesizeSpeech, transcribeAudio } from "../api";
import { useResource } from "../useApi";
import { loadMissionControl, type MissionControlData } from "../missionControl";
import RockyPresence from "../components/RockyPresence";
import CommandCenterPanels from "../components/CommandCenterPanels";
import type { ConversationResponse, DeviceLocationContext, Reminder } from "../types";
import { startBrowserSpeech, type BrowserSpeechStartResult } from "../browserSpeech";
import { needsDeviceLocation, requestDeviceLocationContext } from "../locationContext";
import { browserTimezone } from "../format";
import {
  evaluateVoiceActivityLevel,
  inspectVoiceActivityLevel,
  initialVoiceActivityState,
  isDegenerateNativeCapture,
  MAX_RECORDING_MS,
  MIN_RECORDING_MS,
  rawSignalStatsFromByteTimeDomain,
  recordingAudioConstraints,
  shouldRejectDegenerateNativeCapture,
  shouldReacquireSilentNativeCapture,
  shouldStopExhaustedNativeCaptureRecovery,
  terminalPlaybackRuntimeState,
  terminalRecordingRuntimeState,
  validateRecordingForTranscription,
  type VoiceActivityState,
} from "../voiceActivity";

interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  0: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionResultEventLike {
  results: {
    0: SpeechRecognitionResultLike;
  };
}

interface SpeechRecognitionErrorEventLike {
  error: string;
}

interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onresult: ((event: SpeechRecognitionResultEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  start: () => void;
  stop: () => void;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

type SpeechWindow = Window & {
  SpeechRecognition?: SpeechRecognitionCtor;
  webkitSpeechRecognition?: SpeechRecognitionCtor;
  webkitAudioContext?: typeof AudioContext;
};

type InteractionState = "idle" | "listening" | "transcribing" | "thinking" | "speaking";

interface RecordingDiagnostics {
  analyserMinRms: number;
  analyserMaxRms: number;
  maxAbsoluteSample: number;
  chunkSizes: number[];
  detectorFrameCount: number;
  zeroSignalFrameCount: number;
}

const MAX_AUTO_SPEECH_CHARS = 1200;
const VALID_SPEECH_TYPES = new Set([
  "audio/wav",
  "audio/wave",
  "audio/x-wav",
  "audio/mpeg",
  "audio/mp4",
  "audio/aac",
  "audio/ogg",
]);
const SPEECH_DEBUG =
  typeof window !== "undefined"
  && (import.meta.env.DEV || import.meta.env.VITE_SPEECH_DEBUG === "true");
const SPEECH_DIAGNOSTICS =
  typeof window !== "undefined"
  && import.meta.env.VITE_SPEECH_DEBUG === "true";
const SPEECH_DISABLE_AUDIO_PROCESSING =
  typeof window !== "undefined"
  && import.meta.env.VITE_SPEECH_DISABLE_AUDIO_PROCESSING === "true";
const SILENT_CAPTURE_REACQUIRE_MS = 1_600;
const SILENT_CAPTURE_REACQUIRE_MIN_FRAMES = 20;
const SILENT_CAPTURE_REACQUIRE_ZERO_FRAME_RATIO = 0.85;
const DEGENERATE_CAPTURE_REJECT_MS = 1_600;
const DEGENERATE_CAPTURE_REJECT_MIN_FRAMES = 20;
const DEGENERATE_CAPTURE_ZERO_FRAME_RATIO = 0.85;
const DEGENERATE_CAPTURE_MAX_PEAK_RMS = 0.05;
const MAX_SILENT_CAPTURE_REACQUIRE_ATTEMPTS = 2;
const CAPTURE_REACQUIRE_STOP_REASON = "silent-capture-reacquire";
const CAPTURE_RECOVERY_EXHAUSTED_STOP_REASON = "capture-recovery-exhausted";
const RECORDING_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
];
const SPOKEN_OUTPUT_KEY = "rocky.spoken-output";

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function stateCopy(state: InteractionState): { label: string; detail: string } {
  switch (state) {
    case "listening":
      return { label: "Listening", detail: "Recording your voice" };
    case "thinking":
      return { label: "Thinking", detail: "Working on that" };
    case "transcribing":
      return { label: "Transcribing", detail: "Turning your voice into text" };
    case "speaking":
      return { label: "Speaking", detail: "Rocky is speaking" };
    case "idle":
      return { label: "Ready", detail: "Rocky is ready when you are" };
  }
}

function speechRecognitionCtor(): SpeechRecognitionCtor | null {
  const w = window as SpeechWindow;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function mediaRecorderCtor(): typeof MediaRecorder | null {
  return typeof window !== "undefined" && "MediaRecorder" in window
    ? window.MediaRecorder
    : null;
}

function recordingMimeType(): string | undefined {
  const MediaRecorderClass = mediaRecorderCtor();
  if (!MediaRecorderClass) return undefined;
  return RECORDING_MIME_TYPES.find((type) => MediaRecorderClass.isTypeSupported(type));
}

function recordingFilename(type: string | undefined): string {
  if (type?.includes("mp4")) return "rocky-voice.mp4";
  if (type?.includes("ogg")) return "rocky-voice.ogg";
  if (type?.includes("aac")) return "rocky-voice.aac";
  if (type?.includes("wav")) return "rocky-voice.wav";
  return "rocky-voice.webm";
}

function initialSpokenOutput(): boolean {
  try {
    return window.localStorage.getItem(SPOKEN_OUTPUT_KEY) !== "false";
  } catch {
    return true;
  }
}

function microphoneErrorMessage(error: unknown): string {
  if (!window.isSecureContext) return "Microphone access requires a secure connection.";
  if (!(error instanceof DOMException)) return "Microphone could not start. Please try again.";
  if (error.name === "NotAllowedError" || error.name === "SecurityError") {
    return "Microphone access wasn't allowed. You can keep typing.";
  }
  if (error.name === "NotFoundError" || error.name === "DevicesNotFoundError") {
    return "No microphone was found. You can keep typing.";
  }
  if (error.name === "NotReadableError" || error.name === "TrackStartError") {
    return "The microphone is busy. Close other audio apps and try again.";
  }
  if (error.name === "AbortError") return "Microphone startup was interrupted. Please try again.";
  return "Microphone could not start. Please try again.";
}

function recognitionErrorMessage(error: string): string {
  if (error === "not-allowed" || error === "service-not-allowed") {
    return "Microphone access wasn't allowed. You can keep typing.";
  }
  if (error === "audio-capture") return "No available microphone was found. You can keep typing.";
  if (error === "no-speech") return "I couldn't hear anything to send.";
  return "Voice input stopped. Please try again or keep typing.";
}

function usesCoarsePointer(): boolean {
  return window.matchMedia?.("(pointer: coarse)").matches || navigator.maxTouchPoints > 0;
}

function audioContextCtor(): typeof AudioContext | null {
  const w = window as SpeechWindow;
  return window.AudioContext ?? w.webkitAudioContext ?? null;
}

function isNativeSpeechCapture(): boolean {
  return Capacitor.isNativePlatform();
}

function monotonicNow(): number {
  return performance.now();
}

function requestTimingStarted(route: string): { route: string; startedAt: number; startedAtIso: string } {
  return {
    route,
    startedAt: monotonicNow(),
    startedAtIso: new Date().toISOString(),
  };
}

function requestFailureReason(error: unknown, signal?: AbortSignal): string {
  if (signal?.aborted) return "abort";
  if (error instanceof AuthExpiredError) return "auth-expired";
  if (error instanceof ApiError) return `api-${error.status}`;
  if (error instanceof DOMException) return error.name;
  if (error instanceof Error) return error.name;
  return typeof error;
}

function formatSpeechMetadata(metadata: object): string {
  if (Object.keys(metadata).length === 0) return "";
  try {
    return ` ${JSON.stringify(metadata)}`;
  } catch {
    return " {\"metadata\":\"unserializable\"}";
  }
}

function speechDebug(message: string, metadata: object = {}) {
  if (SPEECH_DEBUG) {
    console.info(`[Rocky speech] ${message}${formatSpeechMetadata(metadata)}`);
  }
}

function speechDiagnostic(message: string, metadata: object = {}) {
  if (SPEECH_DIAGNOSTICS) {
    console.info(`[Rocky speech diagnostic] ${message}${formatSpeechMetadata(metadata)}`);
  }
}

async function logAudioInputDevices(recordingSessionId: number) {
  if (!SPEECH_DIAGNOSTICS || !navigator.mediaDevices?.enumerateDevices) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    speechDiagnostic("audio devices", {
      recordingSessionId,
      audioInputs: devices
        .filter((device) => device.kind === "audioinput")
        .map((device) => ({
          kind: device.kind,
          label: device.label,
          deviceId: device.deviceId,
          groupId: device.groupId,
        })),
    });
  } catch (error) {
    speechDiagnostic("audio devices unavailable", {
      recordingSessionId,
      name: error instanceof Error ? error.name : typeof error,
      message: error instanceof Error ? error.message : String(error),
    });
  }
}

function roundedAudioLevel(value: number): number {
  return Number(value.toFixed(6));
}

function RockyInteraction({
  onMutatingAction,
  name,
  summary,
}: {
  onMutatingAction: () => void;
  name: string | null;
  summary: string;
}) {
  const [draft, setDraft] = useState("");
  const [response, setResponse] = useState<ConversationResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  const [listening, setListening] = useState(false);
  const [micStarting, setMicStarting] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [spokenOutput, setSpokenOutput] = useState(initialSpokenOutput);
  const spokenOutputRef = useRef(spokenOutput);
  const [interactionState, setInteractionState] = useState<InteractionState>("idle");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recordingSessionIdRef = useRef(0);
  const audioChunksRef = useRef<Blob[]>([]);
  const recordingDiagnosticsRef = useRef<RecordingDiagnostics>({
    analyserMinRms: Number.POSITIVE_INFINITY,
    analyserMaxRms: 0,
    maxAbsoluteSample: 0,
    chunkSizes: [],
    detectorFrameCount: 0,
    zeroSignalFrameCount: 0,
  });
  const voiceAudioContextRef = useRef<AudioContext | null>(null);
  const voiceSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const voiceAnalyserRef = useRef<AnalyserNode | null>(null);
  const voiceFrameRef = useRef<number | null>(null);
  const voiceDiagnosticTimerRef = useRef<number | null>(null);
  const maxRecordingTimerRef = useRef<number | null>(null);
  const recordingStartedAtRef = useRef(0);
  const recordingStopRequestedAtRef = useRef<number | null>(null);
  const recordingStopReasonRef = useRef<string | null>(null);
  const voiceRoundTripStartedAtRef = useRef<number | null>(null);
  const voiceActivityRef = useRef<VoiceActivityState>(initialVoiceActivityState());
  const silentCaptureReacquireAttemptsRef = useRef(0);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const latestResponseRef = useRef<HTMLDivElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const speechAbortRef = useRef<AbortController | null>(null);
  const speechIdRef = useRef(0);
  const browserSpeechTimerRef = useRef<number | null>(null);
  const requestAbortRef = useRef<AbortController | null>(null);
  const turnIdRef = useRef(0);
  const turnLockedRef = useRef(false);
  const recordingStartingRef = useRef(false);
  const lifecycleActiveRef = useRef(true);
  const followResponseRef = useRef(true);
  const speechSupported = typeof window !== "undefined" && "speechSynthesis" in window;
  const recognitionSupported = typeof window !== "undefined" && speechRecognitionCtor() !== null;
  const recordingSupported = typeof navigator !== "undefined"
    && !!navigator.mediaDevices?.getUserMedia
    && mediaRecorderCtor() !== null;
  const voiceSupported = recordingSupported || recognitionSupported;
  const voiceUnavailableMessage = typeof window !== "undefined" && !window.isSecureContext
    ? "Voice input requires a secure connection. Typing is still available."
    : "Voice input is unavailable in this browser. Typing is still available.";
  const currentState = stateCopy(interactionState);
  const visualState = interactionState !== "idle"
    ? interactionState
    : voiceError || error ? "error" : "idle";
  const hasDraft = draft.trim().length > 0;

  const resizeComposer = useCallback(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    const contentHeight = input.scrollHeight;
    input.style.height = `${Math.min(contentHeight, 132)}px`;
    input.style.overflowY = contentHeight > 132 ? "auto" : "hidden";
  }, []);

  const cleanupVoiceDetection = useCallback(() => {
    if (voiceFrameRef.current !== null) {
      window.cancelAnimationFrame(voiceFrameRef.current);
      voiceFrameRef.current = null;
    }
    if (voiceDiagnosticTimerRef.current !== null) {
      window.clearInterval(voiceDiagnosticTimerRef.current);
      voiceDiagnosticTimerRef.current = null;
    }
    if (maxRecordingTimerRef.current !== null) {
      window.clearTimeout(maxRecordingTimerRef.current);
      maxRecordingTimerRef.current = null;
    }
    const context = voiceAudioContextRef.current;
    voiceAudioContextRef.current = null;
    try {
      voiceSourceRef.current?.disconnect();
    } catch (error) {
      speechDiagnostic("voice source disconnect failed", {
        name: error instanceof Error ? error.name : typeof error,
        message: error instanceof Error ? error.message : String(error),
      });
    }
    voiceSourceRef.current = null;
    try {
      voiceAnalyserRef.current?.disconnect();
    } catch (error) {
      speechDiagnostic("voice analyser disconnect failed", {
        name: error instanceof Error ? error.name : typeof error,
        message: error instanceof Error ? error.message : String(error),
      });
    }
    voiceAnalyserRef.current = null;
    if (context && context.state !== "closed") {
      void context.close().catch((error: unknown) => {
        speechDiagnostic("voice audio context close failed", {
          name: error instanceof Error ? error.name : typeof error,
          message: error instanceof Error ? error.message : String(error),
        });
      });
    }
  }, []);

  const stopMediaStream = useCallback(() => {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
  }, []);

  const cleanupRecording = useCallback(() => {
    cleanupVoiceDetection();
    stopMediaStream();
    mediaRecorderRef.current = null;
  }, [cleanupVoiceDetection, stopMediaStream]);

  const resetRecordingTerminalState = useCallback(() => {
    audioChunksRef.current = [];
    recordingStartingRef.current = false;
    setMicStarting(false);
    setListening(false);
    setInteractionState("idle");
    try {
      cleanupRecording();
    } catch (error) {
      speechDiagnostic("recording cleanup failed", {
        name: error instanceof Error ? error.name : typeof error,
        message: error instanceof Error ? error.message : String(error),
      });
      mediaRecorderRef.current = null;
    }
  }, [cleanupRecording]);

  const reacquireRecordingCapture = (recordingSessionId: number) => {
    speechDiagnostic("recording terminal state", {
      recordingSessionId,
      ...terminalRecordingRuntimeState("cancelled"),
    });
    resetRecordingTerminalState();
    window.setTimeout(() => {
      void startRecordingCapture(true);
    }, 0);
  };

  const closePlaybackContext = useCallback(() => {
    const context = audioContextRef.current;
    audioContextRef.current = null;
    if (context && context.state !== "closed") {
      speechDiagnostic("playback audio context closing", { state: context.state });
      void context.close().catch((error: unknown) => {
        speechDiagnostic("playback audio context close failed", {
          name: error instanceof Error ? error.name : typeof error,
          message: error instanceof Error ? error.message : String(error),
        });
      });
    }
  }, []);

  const stopRockySpeech = useCallback((options: { releasePlaybackContext?: boolean } = {}) => {
    speechIdRef.current += 1;
    speechAbortRef.current?.abort();
    speechAbortRef.current = null;
    if (browserSpeechTimerRef.current !== null) {
      window.clearTimeout(browserSpeechTimerRef.current);
      browserSpeechTimerRef.current = null;
    }
    if (audioSourceRef.current) {
      audioSourceRef.current.onended = null;
      try {
        audioSourceRef.current.stop();
      } catch {
        /* source may already be stopped */
      }
      audioSourceRef.current = null;
    }
    const synthesis = window.speechSynthesis;
    if (synthesis && (synthesis.speaking || synthesis.pending || synthesis.paused)) {
      synthesis.cancel();
    }
    speechDiagnostic("playback terminal state", terminalPlaybackRuntimeState("interrupted"));
    if (options.releasePlaybackContext) closePlaybackContext();
    setInteractionState((state) => (state === "speaking" ? "idle" : state));
  }, [closePlaybackContext]);

  const cancelActiveTurn = useCallback((updateUi: boolean) => {
    turnIdRef.current += 1;
    requestAbortRef.current?.abort();
    requestAbortRef.current = null;
    turnLockedRef.current = false;
    recordingStartingRef.current = false;
    if (updateUi) {
      setSubmitting(false);
      setListening(false);
      setMicStarting(false);
      setInteractionState("idle");
    }
  }, []);

  useEffect(() => {
    lifecycleActiveRef.current = true;
    const stopVoiceForLifecycle = () => {
      lifecycleActiveRef.current = false;
      const recognition = recognitionRef.current;
      recognitionRef.current = null;
      if (recognition) {
        recognition.onend = null;
        recognition.onerror = null;
        recognition.onresult = null;
        recognition.stop();
      }
      const recorder = mediaRecorderRef.current;
      if (recorder?.state === "recording") {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        recorder.stop();
      }
      speechDiagnostic("recording terminal state", {
        recordingSessionId: recordingSessionIdRef.current,
        ...terminalRecordingRuntimeState("cancelled"),
      });
      cleanupRecording();
      stopRockySpeech({ releasePlaybackContext: true });
      closePlaybackContext();
      cancelActiveTurn(true);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") stopVoiceForLifecycle();
      else lifecycleActiveRef.current = true;
    };
    const onPageShow = () => { lifecycleActiveRef.current = true; };
    const onNativeForeground = () => { lifecycleActiveRef.current = true; };
    window.addEventListener("pagehide", stopVoiceForLifecycle);
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("rocky:native-background", stopVoiceForLifecycle);
    window.addEventListener("rocky:native-foreground", onNativeForeground);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      lifecycleActiveRef.current = false;
      window.removeEventListener("pagehide", stopVoiceForLifecycle);
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("rocky:native-background", stopVoiceForLifecycle);
      window.removeEventListener("rocky:native-foreground", onNativeForeground);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      const recognition = recognitionRef.current;
      recognitionRef.current = null;
      if (recognition) {
        recognition.onend = null;
        recognition.onerror = null;
        recognition.onresult = null;
        recognition.stop();
      }
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.ondataavailable = null;
        mediaRecorderRef.current.onstop = null;
        mediaRecorderRef.current.stop();
      }
      speechDiagnostic("recording terminal state", {
        recordingSessionId: recordingSessionIdRef.current,
        ...terminalRecordingRuntimeState("cancelled"),
      });
      cleanupRecording();
      stopRockySpeech({ releasePlaybackContext: true });
      closePlaybackContext();
      cancelActiveTurn(false);
    };
  }, [cancelActiveTurn, cleanupRecording, closePlaybackContext, stopRockySpeech]);

  useEffect(() => {
    const setBrowserOnline = () => setOnline(navigator.onLine);
    const setNativeOnline = (event: Event) => {
      const detail = (event as CustomEvent<{ online?: boolean }>).detail;
      if (typeof detail?.online === "boolean") setOnline(detail.online);
    };
    window.addEventListener("online", setBrowserOnline);
    window.addEventListener("offline", setBrowserOnline);
    window.addEventListener("rocky:network-status", setNativeOnline);
    return () => {
      window.removeEventListener("online", setBrowserOnline);
      window.removeEventListener("offline", setBrowserOnline);
      window.removeEventListener("rocky:network-status", setNativeOnline);
    };
  }, []);

  useEffect(() => {
    const active = submitting || micStarting || interactionState !== "idle";
    document.documentElement.dataset.voiceActive = active ? "true" : "false";
    window.dispatchEvent(new CustomEvent("rocky:voice-activity", { detail: active }));
    return () => {
      delete document.documentElement.dataset.voiceActive;
      window.dispatchEvent(new CustomEvent("rocky:voice-activity", { detail: false }));
    };
  }, [interactionState, micStarting, submitting]);

  const speakBrowser = async (
    reply: string,
    language: string,
    speechId: number,
  ): Promise<BrowserSpeechStartResult | "unavailable"> => {
    if (!speechSupported) return "unavailable";
    const utterance = new SpeechSynthesisUtterance(reply);
    const browserSpeechState = () => ({
      visibility: document.visibilityState,
      userActivationActive: navigator.userActivation?.isActive,
      userActivationSeen: navigator.userActivation?.hasBeenActive,
      speaking: window.speechSynthesis.speaking,
      pending: window.speechSynthesis.pending,
      paused: window.speechSynthesis.paused,
    });
    speechDebug("browser speech preparing", browserSpeechState());
    utterance.onstart = () => {
      speechDebug("browser speech started", browserSpeechState());
      if (speechIdRef.current === speechId) {
        setVoiceError("Neural speech is unavailable; using browser voice.");
      }
    };
    utterance.onend = () => {
      speechDebug("browser speech ended", browserSpeechState());
      if (speechIdRef.current === speechId) {
        if (browserSpeechTimerRef.current !== null) window.clearTimeout(browserSpeechTimerRef.current);
        browserSpeechTimerRef.current = null;
        speechDiagnostic("playback terminal state", terminalPlaybackRuntimeState("ended"));
        setInteractionState("idle");
      }
    };
    utterance.onerror = (event) => {
      speechDebug("browser speech failed", {
        ...browserSpeechState(),
        error: event.error,
      });
      if (speechIdRef.current === speechId) {
        if (browserSpeechTimerRef.current !== null) window.clearTimeout(browserSpeechTimerRef.current);
        browserSpeechTimerRef.current = null;
        speechDiagnostic("playback terminal state", terminalPlaybackRuntimeState("interrupted"));
        setInteractionState("idle");
        setVoiceError("Browser voice couldn't play. The text response is still available; use Replay to try again.");
      }
    };
    utterance.onpause = () => {
      speechDebug("browser speech paused", browserSpeechState());
    };
    utterance.onresume = () => {
      speechDebug("browser speech resumed", browserSpeechState());
    };
    setInteractionState("speaking");
    const result = await startBrowserSpeech({
      engine: window.speechSynthesis,
      utterance,
      language,
      enabled: spokenOutputRef.current,
      isCurrent: () => lifecycleActiveRef.current && speechIdRef.current === speechId,
    });
    if (result === "started") {
      speechDebug("browser speech queued", browserSpeechState());
      browserSpeechTimerRef.current = window.setTimeout(() => {
        if (speechIdRef.current !== speechId) return;
        window.speechSynthesis.cancel();
        browserSpeechTimerRef.current = null;
        speechDiagnostic("playback terminal state", terminalPlaybackRuntimeState("interrupted"));
        setInteractionState("idle");
        setVoiceError("Audio playback stopped. The text response is still available; use Replay to try again.");
      }, Math.max(15_000, Math.min(120_000, reply.length * 90)));
    } else if (result === "failed") {
      speechDebug("browser speech could not be queued", browserSpeechState());
      setInteractionState("idle");
      setVoiceError("Audio couldn't start. The text response is still available; use Replay to try again.");
    } else {
      setInteractionState("idle");
    }
    return result;
  };

  const ensureAudioContext = async (): Promise<AudioContext> => {
    if (!audioContextRef.current) {
      const AudioContextClass = audioContextCtor();
      if (!AudioContextClass) {
        throw new Error("audio_context_unavailable");
      }
      audioContextRef.current = new AudioContextClass();
    }
    if (audioContextRef.current.state === "suspended") {
      await audioContextRef.current.resume();
    }
    return audioContextRef.current;
  };

  const unlockAudioPlayback = () => {
    void ensureAudioContext().then(
      (ctx) => speechDebug("audio context ready", { state: ctx.state }),
      (error: unknown) => speechDebug("audio context unlock failed", {
        name: error instanceof Error ? error.name : typeof error,
        message: error instanceof Error ? error.message : String(error),
      }),
    );
  };

  const playNeuralSpeech = async (
    audio: Blob,
    controller: AbortController,
    speechId: number,
  ) => {
    const ctx = await ensureAudioContext();
    if (controller.signal.aborted || speechIdRef.current !== speechId) return;
    const buffer = await audio.arrayBuffer();
    if (controller.signal.aborted || speechIdRef.current !== speechId) return;
    const decoded = await ctx.decodeAudioData(buffer);
    if (controller.signal.aborted || speechIdRef.current !== speechId) return;

    const source = ctx.createBufferSource();
    source.buffer = decoded;
    source.connect(ctx.destination);
    audioSourceRef.current = source;
    setInteractionState("speaking");
    source.onended = () => {
      if (audioSourceRef.current === source && speechIdRef.current === speechId) {
        audioSourceRef.current = null;
        speechDiagnostic("playback terminal state", terminalPlaybackRuntimeState("ended"));
        closePlaybackContext();
        setInteractionState("idle");
      }
    };
    source.start();
    const now = performance.now();
    speechDebug("audio playback started", {
      roundTripMs: voiceRoundTripStartedAtRef.current === null
        ? undefined
        : Math.round(now - voiceRoundTripStartedAtRef.current),
    });
  };

  const speak = async (reply: string, language: string) => {
    if (!spokenOutputRef.current || !lifecycleActiveRef.current) return;
    stopRockySpeech();
    const speechId = speechIdRef.current;
    if (reply.length > MAX_AUTO_SPEECH_CHARS) {
      setVoiceError("Rocky's reply is too long for automatic speech.");
      return;
    }

    const controller = new AbortController();
    speechAbortRef.current = controller;
    setInteractionState("speaking");
    const speechTiming = requestTimingStarted("/speech");
    speechDebug("speech request started", {
      route: speechTiming.route,
      startedAt: speechTiming.startedAtIso,
    });
    try {
      const response = await synthesizeSpeech({ text: reply, language }, controller.signal);
      if (controller.signal.aborted || speechIdRef.current !== speechId) return;
      speechDebug("speech response", {
        route: speechTiming.route,
        startedAt: speechTiming.startedAtIso,
        elapsedMs: Math.round(monotonicNow() - speechTiming.startedAt),
        success: true,
        status: response.status,
        contentType: response.contentType,
        blobSize: response.blob.size,
      });
      if (!VALID_SPEECH_TYPES.has(response.contentType)) {
        throw new Error(`invalid_content_type:${response.contentType}`);
      }
      if (response.blob.size === 0) {
        throw new Error("empty_blob");
      }
      await playNeuralSpeech(response.blob, controller, speechId);
    } catch (e: unknown) {
      if (controller.signal.aborted || speechIdRef.current !== speechId) {
        speechDebug("speech request failed", {
          route: speechTiming.route,
          startedAt: speechTiming.startedAtIso,
          elapsedMs: Math.round(monotonicNow() - speechTiming.startedAt),
          success: false,
          reason: requestFailureReason(e, controller.signal),
          aborted: controller.signal.aborted,
        });
        return;
      }
      speechDebug("speech request failed", {
        route: speechTiming.route,
        startedAt: speechTiming.startedAtIso,
        elapsedMs: Math.round(monotonicNow() - speechTiming.startedAt),
        success: false,
        reason: requestFailureReason(e, controller.signal),
        aborted: controller.signal.aborted,
        name: e instanceof Error ? e.name : typeof e,
        message: e instanceof Error ? e.message : String(e),
      });
      stopRockySpeech({ releasePlaybackContext: true });
      const browserSpeechId = speechIdRef.current;
      const browserResult = await speakBrowser(reply, language || "en", browserSpeechId);
      if (browserResult === "started") {
        setVoiceError(
          e instanceof ApiError
            ? "Neural speech is unavailable; browser voice playback was requested."
            : "Audio playback was blocked; browser voice playback was requested.",
        );
      } else {
        setVoiceError(
          browserResult === "unavailable"
            ? "Browser speech is unavailable. The text response is still available."
            : "Browser speech couldn't start. The text response is still available; use Replay to try again.",
        );
      }
    }
  };

  const beginTurn = (): { id: number; controller: AbortController } | null => {
    if (turnLockedRef.current || recordingStartingRef.current) return null;
    turnLockedRef.current = true;
    const id = turnIdRef.current + 1;
    turnIdRef.current = id;
    const controller = new AbortController();
    requestAbortRef.current = controller;
    setSubmitting(true);
    return { id, controller };
  };

  const isCurrentTurn = (id: number, controller: AbortController): boolean =>
    lifecycleActiveRef.current
    && turnIdRef.current === id
    && requestAbortRef.current === controller
    && !controller.signal.aborted;

  const finishTurn = (id: number, controller: AbortController) => {
    if (turnIdRef.current !== id || requestAbortRef.current !== controller) return;
    requestAbortRef.current = null;
    turnLockedRef.current = false;
    setSubmitting(false);
    setInteractionState((state) =>
      state === "thinking" || state === "transcribing" ? "idle" : state,
    );
  };

  const runConversation = async (
    text: string,
    id: number,
    controller: AbortController,
    language?: string | null,
    locationContext?: DeviceLocationContext | null,
  ) => {
    setInteractionState("thinking");
    const conversationTiming = requestTimingStarted("/conversation");
    speechDebug("conversation request started", {
      route: conversationTiming.route,
      startedAt: conversationTiming.startedAtIso,
    });
    try {
      const result = await sendConversation({
        message: text,
        timezone: browserTimezone(),
        language,
        location_context: locationContext,
      }, controller.signal);
      if (!isCurrentTurn(id, controller)) return;
      speechDebug("conversation response", {
        route: conversationTiming.route,
        startedAt: conversationTiming.startedAtIso,
        elapsedMs: Math.round(monotonicNow() - conversationTiming.startedAt),
        success: true,
      });
      setResponse(result);
      setInteractionState("idle");
      if (!usesCoarsePointer()) inputRef.current?.focus();
      const composerRect = inputRef.current?.getBoundingClientRect();
      if (followResponseRef.current && (!composerRect || (composerRect.bottom >= 0 && composerRect.top <= window.innerHeight))) {
        window.requestAnimationFrame(() => latestResponseRef.current?.scrollIntoView({
          block: "nearest",
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        }));
      }
      if (result.executed) onMutatingAction();
      finishTurn(id, controller);
      void speak(result.reply, result.language);
    } catch (e: unknown) {
      if (!isCurrentTurn(id, controller)) {
        if (controller.signal.aborted) {
          speechDebug("conversation request failed", {
            route: conversationTiming.route,
            startedAt: conversationTiming.startedAtIso,
            elapsedMs: Math.round(monotonicNow() - conversationTiming.startedAt),
            success: false,
            reason: requestFailureReason(e, controller.signal),
            aborted: controller.signal.aborted,
          });
        }
        return;
      }
      speechDebug("conversation request failed", {
        route: conversationTiming.route,
        startedAt: conversationTiming.startedAtIso,
        elapsedMs: Math.round(monotonicNow() - conversationTiming.startedAt),
        success: false,
        reason: requestFailureReason(e, controller.signal),
        aborted: controller.signal.aborted,
        name: e instanceof Error ? e.name : typeof e,
        message: e instanceof Error ? e.message : String(e),
      });
      if (e instanceof AuthExpiredError) {
        setError("Your session expired. Please sign in again.");
      } else {
        setError(e instanceof Error ? e.message : "Rocky could not respond.");
      }
      setInteractionState("idle");
    } finally {
      finishTurn(id, controller);
    }
  };

  const submitMessage = async (message: string) => {
    const text = message.trim();
    if (!text) return;
    if (!online) {
      setError("Rocky needs the backend for that. Please reconnect and try again.");
      return;
    }
    let locationContext: DeviceLocationContext | null = null;
    if (needsDeviceLocation(text)) {
      const location = await requestDeviceLocationContext();
      if (location.status !== "granted" || !location.context) {
        setError("Please include a city or place in your message so Rocky can answer without device location.");
        return;
      }
      locationContext = location.context;
    }
    const turn = beginTurn();
    if (!turn) return;
    stopRockySpeech();
    setError(null);
    setVoiceError(null);
    setDraft("");
    window.requestAnimationFrame(resizeComposer);
    const rect = inputRef.current?.getBoundingClientRect();
    followResponseRef.current = !rect || (rect.bottom >= 0 && rect.top <= window.innerHeight);
    await runConversation(text, turn.id, turn.controller, null, locationContext);
  };

  const submitTranscription = async (audio: Blob, filename: string) => {
    const turn = beginTurn();
    if (!turn) return;
    const transcriptionTiming = requestTimingStarted("/transcribe");
    try {
      setInteractionState("transcribing");
      setVoiceError(null);
      speechDebug("transcription request started", {
        route: transcriptionTiming.route,
        startedAt: transcriptionTiming.startedAtIso,
        blobSize: audio.size,
      });
      const result = await transcribeAudio(audio, filename, turn.controller.signal);
      if (!isCurrentTurn(turn.id, turn.controller)) return;
      speechDebug("transcription response", {
        route: transcriptionTiming.route,
        startedAt: transcriptionTiming.startedAtIso,
        elapsedMs: Math.round(monotonicNow() - transcriptionTiming.startedAt),
        success: true,
      });
      const text = result.text.trim();
      if (!text) {
        setVoiceError("I couldn't hear anything to send.");
        setInteractionState("idle");
        return;
      }
      let locationContext: DeviceLocationContext | null = null;
      if (needsDeviceLocation(text)) {
        const location = await requestDeviceLocationContext();
        if (location.status !== "granted" || !location.context) {
          setVoiceError("Please say a city or place so Rocky can answer without device location.");
          setInteractionState("idle");
          return;
        }
        locationContext = location.context;
      }
      await runConversation(text, turn.id, turn.controller, result.language, locationContext);
    } catch (e: unknown) {
      if (!isCurrentTurn(turn.id, turn.controller)) {
        if (turn.controller.signal.aborted) {
          speechDebug("transcription request failed", {
            route: transcriptionTiming.route,
            startedAt: transcriptionTiming.startedAtIso,
            elapsedMs: Math.round(monotonicNow() - transcriptionTiming.startedAt),
            success: false,
            reason: requestFailureReason(e, turn.controller.signal),
            aborted: turn.controller.signal.aborted,
          });
        }
        return;
      }
      speechDebug("transcription request failed", {
        route: transcriptionTiming.route,
        startedAt: transcriptionTiming.startedAtIso,
        elapsedMs: Math.round(monotonicNow() - transcriptionTiming.startedAt),
        success: false,
        reason: requestFailureReason(e, turn.controller.signal),
        aborted: turn.controller.signal.aborted,
        name: e instanceof Error ? e.name : typeof e,
        message: e instanceof Error ? e.message : String(e),
      });
      if (e instanceof AuthExpiredError) {
        setError("Your session expired. Please sign in again.");
      } else if (e instanceof ApiError) {
        setVoiceError(e.message || "I couldn't transcribe that audio.");
      } else {
        setVoiceError("Recording could not be transcribed. Please try again.");
      }
      setInteractionState("idle");
    } finally {
      finishTurn(turn.id, turn.controller);
    }
  };

  const stopActiveRecorder = (reason = "manual") => {
    const recorder = mediaRecorderRef.current;
    if (recorder?.state === "recording") {
      const now = performance.now();
      recordingStopRequestedAtRef.current = now;
      recordingStopReasonRef.current = reason;
      voiceRoundTripStartedAtRef.current = now;
      speechDebug("recording stop requested", {
        recordingSessionId: recordingSessionIdRef.current,
        reason,
        silenceMs: voiceActivityRef.current.silenceStartedAt === null
          ? undefined
          : Math.round(now - voiceActivityRef.current.silenceStartedAt),
      });
      recorder.stop();
    }
  };

  const startSilenceDetection = async (
    stream: MediaStream,
    recorder: MediaRecorder,
  ) => {
    const AudioContextClass = audioContextCtor();
    if (!AudioContextClass) return;

    const context = new AudioContextClass();
    voiceAudioContextRef.current = context;
    if (context.state === "suspended") {
      await context.resume();
    }

    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    voiceSourceRef.current = source;
    voiceAnalyserRef.current = analyser;
    analyser.fftSize = 1024;
    source.connect(analyser);
    speechDebug("voice detection started", {
      recordingSessionId: recordingSessionIdRef.current,
      contextState: context.state,
      fftSize: analyser.fftSize,
      sampleRate: context.sampleRate,
      streamId: stream.id,
    });

    const samples = new Uint8Array(analyser.fftSize);
    const logAnalyserDiagnostic = () => {
      analyser.getByteTimeDomainData(samples);
      const rawSignal = rawSignalStatsFromByteTimeDomain(samples);
      const level = rawSignal.rms;
      const now = performance.now();
      recordingDiagnosticsRef.current.analyserMinRms = Math.min(
        recordingDiagnosticsRef.current.analyserMinRms,
        level,
      );
      recordingDiagnosticsRef.current.analyserMaxRms = Math.max(
        recordingDiagnosticsRef.current.analyserMaxRms,
        level,
      );
      recordingDiagnosticsRef.current.maxAbsoluteSample = Math.max(
        recordingDiagnosticsRef.current.maxAbsoluteSample,
        rawSignal.maxAbsoluteSample,
      );
      const inspection = inspectVoiceActivityLevel(
        voiceActivityRef.current,
        level,
        now,
        recordingStartedAtRef.current,
      );
      speechDiagnostic("analyser rms", {
        recordingSessionId: recordingSessionIdRef.current,
        elapsedMs: Math.round(now - recordingStartedAtRef.current),
        currentRms: roundedAudioLevel(inspection.level),
        nonZeroSampleCount: rawSignal.nonZeroSampleCount,
        maxAbsoluteSample: roundedAudioLevel(rawSignal.maxAbsoluteSample),
        rawSignalRms: roundedAudioLevel(rawSignal.rms),
        zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
        rollingMin: Number.isFinite(recordingDiagnosticsRef.current.analyserMinRms)
          ? roundedAudioLevel(recordingDiagnosticsRef.current.analyserMinRms)
          : null,
        rollingMax: roundedAudioLevel(recordingDiagnosticsRef.current.analyserMaxRms),
        ambientLevel: roundedAudioLevel(inspection.ambientLevel),
        speechStartThreshold: roundedAudioLevel(inspection.speechStartThreshold),
        silenceThreshold: roundedAudioLevel(inspection.silenceThreshold),
        speechDetected: inspection.speechDetected,
        peakRms: roundedAudioLevel(voiceActivityRef.current.peakLevel),
        aggregateMaxAbsoluteSample: roundedAudioLevel(
          recordingDiagnosticsRef.current.maxAbsoluteSample,
        ),
        peakElapsedMs: voiceActivityRef.current.peakElapsedMs === null
          ? null
          : Math.round(voiceActivityRef.current.peakElapsedMs),
        speechStartThresholdAtPeak: voiceActivityRef.current.speechStartThresholdAtPeak === null
          ? null
          : roundedAudioLevel(voiceActivityRef.current.speechStartThresholdAtPeak),
        audioContextState: context.state,
        detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
      });
    };
    if (SPEECH_DIAGNOSTICS) {
      logAnalyserDiagnostic();
      voiceDiagnosticTimerRef.current = window.setInterval(logAnalyserDiagnostic, 500);
    }

    const tick = () => {
      if (recorder.state !== "recording") return;
      analyser.getByteTimeDomainData(samples);
      const rawSignal = rawSignalStatsFromByteTimeDomain(samples);
      const level = rawSignal.rms;
      const now = performance.now();
      const elapsedMs = now - recordingStartedAtRef.current;
      recordingDiagnosticsRef.current.detectorFrameCount += 1;
      if (rawSignal.nonZeroSampleCount === 0) {
        recordingDiagnosticsRef.current.zeroSignalFrameCount += 1;
      }
      recordingDiagnosticsRef.current.analyserMinRms = Math.min(
        recordingDiagnosticsRef.current.analyserMinRms,
        level,
      );
      recordingDiagnosticsRef.current.analyserMaxRms = Math.max(
        recordingDiagnosticsRef.current.analyserMaxRms,
        level,
      );
      recordingDiagnosticsRef.current.maxAbsoluteSample = Math.max(
        recordingDiagnosticsRef.current.maxAbsoluteSample,
        rawSignal.maxAbsoluteSample,
      );
      const wasSpeechDetected = voiceActivityRef.current.speechDetected;
      const hadSilenceStarted = voiceActivityRef.current.silenceStartedAt !== null;
      const decision = evaluateVoiceActivityLevel(
        voiceActivityRef.current,
        level,
        now,
        recordingStartedAtRef.current,
      );
      const degenerateNativeCapture = recordingStopReasonRef.current !== "manual"
        && isDegenerateNativeCapture({
          nativePlatform: isNativeSpeechCapture(),
          elapsedMs,
          detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
          zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
          peakRms: voiceActivityRef.current.peakLevel,
          minObservationMs: DEGENERATE_CAPTURE_REJECT_MS,
          minDetectorFrames: DEGENERATE_CAPTURE_REJECT_MIN_FRAMES,
          zeroFrameRatio: DEGENERATE_CAPTURE_ZERO_FRAME_RATIO,
          maxWeakPeakRms: DEGENERATE_CAPTURE_MAX_PEAK_RMS,
        });
      if (
        recordingStopReasonRef.current !== "manual"
        && (
          shouldReacquireSilentNativeCapture({
            nativePlatform: isNativeSpeechCapture(),
            speechDetected: decision.speechDetected,
            elapsedMs,
            detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
            zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
            peakRms: voiceActivityRef.current.peakLevel,
            reacquireAttemptCount: silentCaptureReacquireAttemptsRef.current,
            minObservationMs: SILENT_CAPTURE_REACQUIRE_MS,
            minDetectorFrames: SILENT_CAPTURE_REACQUIRE_MIN_FRAMES,
            zeroFrameRatio: SILENT_CAPTURE_REACQUIRE_ZERO_FRAME_RATIO,
            maxReacquireAttempts: MAX_SILENT_CAPTURE_REACQUIRE_ATTEMPTS,
          })
          || (
            degenerateNativeCapture
            && silentCaptureReacquireAttemptsRef.current
              < MAX_SILENT_CAPTURE_REACQUIRE_ATTEMPTS
          )
        )
      ) {
        const zeroSignalRatio = recordingDiagnosticsRef.current.zeroSignalFrameCount
          / recordingDiagnosticsRef.current.detectorFrameCount;
        silentCaptureReacquireAttemptsRef.current += 1;
        speechDiagnostic("silent capture reacquire requested", {
          recordingSessionId: recordingSessionIdRef.current,
          elapsedMs: Math.round(elapsedMs),
          detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
          zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
          zeroSignalRatio: Number(zeroSignalRatio.toFixed(3)),
          peakRms: roundedAudioLevel(voiceActivityRef.current.peakLevel),
          maxAbsoluteSample: roundedAudioLevel(rawSignal.maxAbsoluteSample),
          aggregateMaxAbsoluteSample: roundedAudioLevel(
            recordingDiagnosticsRef.current.maxAbsoluteSample,
          ),
          reacquireAttemptCount: silentCaptureReacquireAttemptsRef.current,
        });
        stopActiveRecorder(CAPTURE_REACQUIRE_STOP_REASON);
        return;
      }
      if (recordingStopReasonRef.current !== "manual" && shouldStopExhaustedNativeCaptureRecovery({
        nativePlatform: isNativeSpeechCapture(),
        elapsedMs,
        detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
        zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
        peakRms: voiceActivityRef.current.peakLevel,
        reacquireAttemptCount: silentCaptureReacquireAttemptsRef.current,
        minObservationMs: DEGENERATE_CAPTURE_REJECT_MS,
        minDetectorFrames: DEGENERATE_CAPTURE_REJECT_MIN_FRAMES,
        zeroFrameRatio: DEGENERATE_CAPTURE_ZERO_FRAME_RATIO,
        maxWeakPeakRms: DEGENERATE_CAPTURE_MAX_PEAK_RMS,
        maxReacquireAttempts: MAX_SILENT_CAPTURE_REACQUIRE_ATTEMPTS,
      })) {
        const zeroSignalRatio = recordingDiagnosticsRef.current.zeroSignalFrameCount
          / recordingDiagnosticsRef.current.detectorFrameCount;
        speechDiagnostic("capture recovery exhausted", {
          recordingSessionId: recordingSessionIdRef.current,
          reacquireAttempt: true,
          reacquireAttemptCount: silentCaptureReacquireAttemptsRef.current,
          elapsedMs: Math.round(elapsedMs),
          detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
          zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
          zeroSignalRatio: Number(zeroSignalRatio.toFixed(3)),
          peakRms: roundedAudioLevel(voiceActivityRef.current.peakLevel),
          aggregateMaxAbsoluteSample: roundedAudioLevel(
            recordingDiagnosticsRef.current.maxAbsoluteSample,
          ),
        });
        stopActiveRecorder(CAPTURE_RECOVERY_EXHAUSTED_STOP_REASON);
        return;
      }
      if (!wasSpeechDetected && decision.speechDetected) {
        speechDebug("speech detected", {
          ambientLevel: decision.ambientLevel,
          level: decision.level,
          speechStartThreshold: decision.speechStartThreshold,
        });
      } else if (!hadSilenceStarted && decision.silenceStartedAt !== null) {
        speechDebug("silence timer started", {
          ambientLevel: decision.ambientLevel,
          level: decision.level,
          silenceThreshold: decision.silenceThreshold,
        });
      } else if (hadSilenceStarted && decision.silenceStartedAt === null) {
        speechDebug("silence timer reset", {
          level: decision.level,
          silenceThreshold: decision.silenceThreshold,
        });
      }
      if (decision.shouldStop) {
        stopActiveRecorder("silence");
        return;
      }
      voiceFrameRef.current = window.requestAnimationFrame(tick);
    };

    voiceFrameRef.current = window.requestAnimationFrame(tick);
    maxRecordingTimerRef.current = window.setTimeout(
      () => stopActiveRecorder("max-duration"),
      MAX_RECORDING_MS,
    );
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    unlockAudioPlayback();
    const text = draft;
    void submitMessage(text);
  };

  const onComposerKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key !== "Enter"
      || event.shiftKey
      || event.nativeEvent.isComposing
      || usesCoarsePointer()
    ) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  };

  const startRecordingCapture = async (reacquireAttempt: boolean) => {
    if (
      turnLockedRef.current
      || recordingStartingRef.current
      || mediaRecorderRef.current?.state === "recording"
      || recognitionRef.current
    ) return;
    if (!reacquireAttempt) {
      silentCaptureReacquireAttemptsRef.current = 0;
    }
    recordingSessionIdRef.current += 1;
    const recordingSessionId = recordingSessionIdRef.current;
    recordingStartingRef.current = true;
    setMicStarting(true);
    speechDiagnostic("recording session starting", {
      recordingSessionId,
      reacquireAttempt,
      silentCaptureReacquireAttempts: silentCaptureReacquireAttemptsRef.current,
      stalePlaybackContextState: audioContextRef.current?.state ?? null,
      staleCaptureStreamActive: mediaStreamRef.current?.active ?? null,
      staleRecorderState: mediaRecorderRef.current?.state ?? null,
    });
    stopRockySpeech({ releasePlaybackContext: true });
    cleanupRecording();
    if (recordingSupported) {
      let stream: MediaStream | null = null;
      try {
        const audioConstraints = recordingAudioConstraints(SPEECH_DISABLE_AUDIO_PROCESSING);
        speechDiagnostic("get user media requested", {
          recordingSessionId,
          disableAudioProcessing: SPEECH_DISABLE_AUDIO_PROCESSING,
          constraints: audioConstraints,
        });
        stream = await navigator.mediaDevices.getUserMedia(audioConstraints);
        if (!lifecycleActiveRef.current) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        const activeStream = stream;
        mediaStreamRef.current = activeStream;
        void logAudioInputDevices(recordingSessionId);
        const audioTrack = activeStream.getAudioTracks()[0];
        const trackSettings = audioTrack?.getSettings() as Record<string, unknown> | undefined;
        const trackCapabilities = audioTrack?.getCapabilities?.() as Record<string, unknown> | undefined;
        speechDiagnostic("media stream track settings", {
          recordingSessionId,
          streamId: activeStream.id,
          streamActive: activeStream.active,
          trackId: audioTrack?.id,
          trackSettings,
          trackCapabilities,
          sampleRate: trackSettings?.sampleRate,
          channelCount: trackSettings?.channelCount,
          deviceId: trackSettings?.deviceId,
          echoCancellation: trackSettings?.echoCancellation,
          noiseSuppression: trackSettings?.noiseSuppression,
          autoGainControl: trackSettings?.autoGainControl,
          label: audioTrack?.label,
          readyState: audioTrack?.readyState,
          muted: audioTrack?.muted,
          enabled: audioTrack?.enabled,
        });
        audioTrack?.addEventListener("mute", () => {
          speechDiagnostic("media stream track event", {
            recordingSessionId,
            event: "mute",
            streamId: activeStream.id,
            streamActive: activeStream.active,
            trackId: audioTrack.id,
            readyState: audioTrack.readyState,
            enabled: audioTrack.enabled,
            muted: audioTrack.muted,
          });
        });
        audioTrack?.addEventListener("unmute", () => {
          speechDiagnostic("media stream track event", {
            recordingSessionId,
            event: "unmute",
            streamId: activeStream.id,
            streamActive: activeStream.active,
            trackId: audioTrack.id,
            readyState: audioTrack.readyState,
            enabled: audioTrack.enabled,
            muted: audioTrack.muted,
          });
        });
        audioTrack?.addEventListener("ended", () => {
          speechDiagnostic("media stream track event", {
            recordingSessionId,
            event: "ended",
            streamId: activeStream.id,
            streamActive: activeStream.active,
            trackId: audioTrack.id,
            readyState: audioTrack.readyState,
            enabled: audioTrack.enabled,
            muted: audioTrack.muted,
          });
        });
        const mimeType = recordingMimeType();
        let recorder: MediaRecorder;
        try {
          recorder = new MediaRecorder(activeStream, mimeType ? { mimeType } : undefined);
        } catch (error) {
          speechDiagnostic("media recorder mime fallback", {
            requestedMimeType: mimeType,
            name: error instanceof Error ? error.name : typeof error,
            message: error instanceof Error ? error.message : String(error),
          });
          if (!mimeType) throw error;
          recorder = new MediaRecorder(activeStream);
        }
        audioChunksRef.current = [];
        recordingDiagnosticsRef.current = {
          analyserMinRms: Number.POSITIVE_INFINITY,
          analyserMaxRms: 0,
          maxAbsoluteSample: 0,
          chunkSizes: [],
          detectorFrameCount: 0,
          zeroSignalFrameCount: 0,
        };
        mediaRecorderRef.current = recorder;
        recordingStartedAtRef.current = monotonicNow();
        recordingStopRequestedAtRef.current = null;
        recordingStopReasonRef.current = null;
        voiceRoundTripStartedAtRef.current = null;
        voiceActivityRef.current = initialVoiceActivityState();
        speechDiagnostic("media recorder created", {
          recordingSessionId,
          selectedMimeType: recorder.mimeType || mimeType,
          requestedMimeType: mimeType,
          state: recorder.state,
        });

        recorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            audioChunksRef.current.push(event.data);
          }
          recordingDiagnosticsRef.current.chunkSizes.push(event.data.size);
          speechDiagnostic("media recorder chunk", {
            recordingSessionId,
            state: recorder.state,
            chunkCount: recordingDiagnosticsRef.current.chunkSizes.length,
            chunkBytes: event.data.size,
          });
        };
        recorder.onerror = () => {
          recorder.onstop = null;
          speechDiagnostic("media recorder error", {
            recordingSessionId,
            state: recorder.state,
            chunkCount: recordingDiagnosticsRef.current.chunkSizes.length,
            chunkSizes: recordingDiagnosticsRef.current.chunkSizes,
          });
          setVoiceError("Recording failed.");
          resetRecordingTerminalState();
        };
        recorder.onstop = () => {
          const blobReadyAt = performance.now();
          const chunks = audioChunksRef.current;
          const duration = blobReadyAt - recordingStartedAtRef.current;
          const speechDetected = voiceActivityRef.current.speechDetected;
          const type = recorder.mimeType || mimeType || "audio/webm";
          const stopReason = recordingStopReasonRef.current ?? "recorder-ended";
          const audio = new Blob(chunks, { type });
          speechDebug("recording blob ready", {
            recordingSessionId,
            reason: stopReason,
            stopToBlobMs: recordingStopRequestedAtRef.current === null
              ? undefined
              : Math.round(blobReadyAt - recordingStopRequestedAtRef.current),
            recordingMs: Math.round(duration),
          });
          speechDiagnostic("media recorder final blob", {
            recordingSessionId,
            selectedMimeType: type,
            mime: type,
            state: recorder.state,
            stopReason,
            chunkCount: recordingDiagnosticsRef.current.chunkSizes.length,
            chunkSizes: recordingDiagnosticsRef.current.chunkSizes,
            finalBlobSize: audio.size,
            totalBlobSize: audio.size,
            detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
            zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
            recordingMs: Math.round(duration),
            recordingDurationMs: Math.round(duration),
            analyserMinRms: Number.isFinite(recordingDiagnosticsRef.current.analyserMinRms)
              ? Number(recordingDiagnosticsRef.current.analyserMinRms.toFixed(6))
              : null,
            analyserMaxRms: Number(recordingDiagnosticsRef.current.analyserMaxRms.toFixed(6)),
            aggregateMaxAbsoluteSample: Number(
              recordingDiagnosticsRef.current.maxAbsoluteSample.toFixed(6),
            ),
            rollingRmsMin: Number.isFinite(recordingDiagnosticsRef.current.analyserMinRms)
              ? roundedAudioLevel(recordingDiagnosticsRef.current.analyserMinRms)
              : null,
            rollingRmsMax: roundedAudioLevel(recordingDiagnosticsRef.current.analyserMaxRms),
            ambientBaseline: voiceActivityRef.current.ambientLevel === null
              ? null
              : Number(voiceActivityRef.current.ambientLevel.toFixed(6)),
            peakRms: Number(voiceActivityRef.current.peakLevel.toFixed(6)),
            peakElapsedMs: voiceActivityRef.current.peakElapsedMs === null
              ? null
              : Math.round(voiceActivityRef.current.peakElapsedMs),
            speechStartThresholdAtPeak: voiceActivityRef.current.speechStartThresholdAtPeak === null
              ? null
              : Number(voiceActivityRef.current.speechStartThresholdAtPeak.toFixed(6)),
            speechDetected,
          });
          if (stopReason === CAPTURE_REACQUIRE_STOP_REASON) {
            reacquireRecordingCapture(recordingSessionId);
            return;
          }
          if (stopReason === CAPTURE_RECOVERY_EXHAUSTED_STOP_REASON) {
            speechDiagnostic("recording terminal state", {
              recordingSessionId,
              ...terminalRecordingRuntimeState("rejected"),
            });
            setVoiceError("I couldn't get a clean microphone signal. Please try again.");
            resetRecordingTerminalState();
            return;
          }
          const zeroSignalRatio = recordingDiagnosticsRef.current.detectorFrameCount === 0
            ? 0
            : recordingDiagnosticsRef.current.zeroSignalFrameCount
              / recordingDiagnosticsRef.current.detectorFrameCount;
          const degenerateNativeCapture = stopReason !== "manual" && isDegenerateNativeCapture({
            nativePlatform: isNativeSpeechCapture(),
            elapsedMs: duration,
            detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
            zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
            peakRms: voiceActivityRef.current.peakLevel,
            minObservationMs: DEGENERATE_CAPTURE_REJECT_MS,
            minDetectorFrames: DEGENERATE_CAPTURE_REJECT_MIN_FRAMES,
            zeroFrameRatio: DEGENERATE_CAPTURE_ZERO_FRAME_RATIO,
            maxWeakPeakRms: DEGENERATE_CAPTURE_MAX_PEAK_RMS,
          });
          if (degenerateNativeCapture && shouldRejectDegenerateNativeCapture({
            nativePlatform: isNativeSpeechCapture(),
            elapsedMs: duration,
            detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
            zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
            peakRms: voiceActivityRef.current.peakLevel,
            reacquireAttemptCount: silentCaptureReacquireAttemptsRef.current,
            minObservationMs: DEGENERATE_CAPTURE_REJECT_MS,
            minDetectorFrames: DEGENERATE_CAPTURE_REJECT_MIN_FRAMES,
            zeroFrameRatio: DEGENERATE_CAPTURE_ZERO_FRAME_RATIO,
            maxWeakPeakRms: DEGENERATE_CAPTURE_MAX_PEAK_RMS,
            maxReacquireAttempts: MAX_SILENT_CAPTURE_REACQUIRE_ATTEMPTS,
          })) {
            silentCaptureReacquireAttemptsRef.current += 1;
            speechDiagnostic("degenerate capture rejected", {
              recordingSessionId,
              detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
              zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
              zeroSignalRatio: Number(zeroSignalRatio.toFixed(3)),
              peakRms: Number(voiceActivityRef.current.peakLevel.toFixed(6)),
              analyserMinRms: Number.isFinite(recordingDiagnosticsRef.current.analyserMinRms)
                ? Number(recordingDiagnosticsRef.current.analyserMinRms.toFixed(6))
                : null,
              analyserMaxRms: Number(recordingDiagnosticsRef.current.analyserMaxRms.toFixed(6)),
              aggregateMaxAbsoluteSample: Number(
                recordingDiagnosticsRef.current.maxAbsoluteSample.toFixed(6),
              ),
              blobSize: audio.size,
              stopReason,
              reacquireAttempt,
              nextReacquireAttempt: true,
              reacquireAttemptCount: silentCaptureReacquireAttemptsRef.current,
            });
            reacquireRecordingCapture(recordingSessionId);
            return;
          }
          if (degenerateNativeCapture) {
            speechDiagnostic("degenerate capture rejected", {
              recordingSessionId,
              detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
              zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
              zeroSignalRatio: Number(zeroSignalRatio.toFixed(3)),
              peakRms: Number(voiceActivityRef.current.peakLevel.toFixed(6)),
              analyserMinRms: Number.isFinite(recordingDiagnosticsRef.current.analyserMinRms)
                ? Number(recordingDiagnosticsRef.current.analyserMinRms.toFixed(6))
                : null,
              analyserMaxRms: Number(recordingDiagnosticsRef.current.analyserMaxRms.toFixed(6)),
              aggregateMaxAbsoluteSample: Number(
                recordingDiagnosticsRef.current.maxAbsoluteSample.toFixed(6),
              ),
              blobSize: audio.size,
              stopReason,
              reacquireAttempt,
              nextReacquireAttempt: false,
              reacquireAttemptCount: silentCaptureReacquireAttemptsRef.current,
            });
            speechDiagnostic("recording terminal state", {
              recordingSessionId,
              ...terminalRecordingRuntimeState("rejected"),
            });
            setVoiceError("I couldn't get a clean microphone signal. Please try again.");
            resetRecordingTerminalState();
            return;
          }
          const validation = validateRecordingForTranscription({
            speechDetected,
            durationMs: duration,
            minRecordingMs: MIN_RECORDING_MS,
            chunkCount: chunks.length,
            blobSize: audio.size,
          });
          if (!validation.accepted) {
            speechDiagnostic("recording rejected", {
              recordingSessionId,
              reason: validation.reason,
              stopReason,
              recordingMs: Math.round(duration),
              minRecordingMs: MIN_RECORDING_MS,
              chunkCount: chunks.length,
              chunkSizes: recordingDiagnosticsRef.current.chunkSizes,
              finalBlobSize: audio.size,
              detectorFrameCount: recordingDiagnosticsRef.current.detectorFrameCount,
              zeroSignalFrameCount: recordingDiagnosticsRef.current.zeroSignalFrameCount,
              analyserMinRms: Number.isFinite(recordingDiagnosticsRef.current.analyserMinRms)
                ? Number(recordingDiagnosticsRef.current.analyserMinRms.toFixed(6))
                : null,
              analyserMaxRms: Number(recordingDiagnosticsRef.current.analyserMaxRms.toFixed(6)),
              aggregateMaxAbsoluteSample: Number(
                recordingDiagnosticsRef.current.maxAbsoluteSample.toFixed(6),
              ),
              ambientBaseline: voiceActivityRef.current.ambientLevel === null
                ? null
                : Number(voiceActivityRef.current.ambientLevel.toFixed(6)),
              peakRms: Number(voiceActivityRef.current.peakLevel.toFixed(6)),
              peakElapsedMs: voiceActivityRef.current.peakElapsedMs === null
                ? null
                : Math.round(voiceActivityRef.current.peakElapsedMs),
              speechStartThresholdAtPeak: voiceActivityRef.current.speechStartThresholdAtPeak === null
                ? null
                : Number(voiceActivityRef.current.speechStartThresholdAtPeak.toFixed(6)),
            });
            speechDiagnostic("recording terminal state", {
              recordingSessionId,
              ...terminalRecordingRuntimeState("rejected"),
            });
            setVoiceError("I couldn't hear anything to send.");
            resetRecordingTerminalState();
            return;
          }
          speechDiagnostic("recording terminal state", {
            recordingSessionId,
            ...terminalRecordingRuntimeState("accepted"),
          });
          resetRecordingTerminalState();
          void submitTranscription(audio, recordingFilename(type));
        };
        recorder.start();
        recordingStartingRef.current = false;
        void startSilenceDetection(activeStream, recorder).catch(() => {
          maxRecordingTimerRef.current = window.setTimeout(
            () => stopActiveRecorder("max-duration"),
            MAX_RECORDING_MS,
          );
        });
        setListening(true);
        setInteractionState("listening");
        setVoiceError(null);
      } catch (e: unknown) {
        stream?.getTracks().forEach((track) => track.stop());
        setVoiceError(microphoneErrorMessage(e));
        setInteractionState("idle");
        setListening(false);
        cleanupRecording();
      } finally {
        recordingStartingRef.current = false;
        if (lifecycleActiveRef.current) setMicStarting(false);
      }
      return;
    }

    const Recognition = speechRecognitionCtor();
    if (!Recognition) {
      recordingStartingRef.current = false;
      setMicStarting(false);
      setVoiceError("Voice input is not available in this browser.");
      return;
    }

    const recognition = new Recognition();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onstart = () => {
      setListening(true);
      setInteractionState("listening");
      setVoiceError(null);
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      setListening(false);
      setInteractionState((state) => (state === "listening" ? "idle" : state));
    };
    recognition.onerror = (event) => {
      recognitionRef.current = null;
      setVoiceError(recognitionErrorMessage(event.error));
      setListening(false);
      setInteractionState("idle");
    };
    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript.trim();
      if (!transcript) {
        setVoiceError("I couldn't hear anything to send.");
        setInteractionState("idle");
        return;
      }
      void submitMessage(transcript);
    };
    recognitionRef.current = recognition;
    recordingStartingRef.current = false;
    setMicStarting(false);
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setVoiceError("Voice input could not start. Please try again or keep typing.");
    }
  };

  const startListening = () => {
    void startRecordingCapture(false);
  };

  const stopListening = () => {
    if (mediaRecorderRef.current?.state === "recording") {
      stopActiveRecorder("manual");
      return;
    }
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setListening(false);
    setInteractionState("idle");
  };

  return (
    <section className="rocky-hero" aria-label="Rocky interaction">
      <div className="rocky-prompt">
        <p className="mission-eyebrow">{new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</p>
        <h1 className="rocky-title">{name ? `${greeting()}, ${name}` : greeting()}</h1>
        <p className="rocky-question">{summary}</p>
      </div>

      <RockyPresence state={visualState === "idle" && response ? "success" : visualState} analyserRef={voiceAnalyserRef} />
      <div className="cc-presence-status" id="rocky-status-detail" role="status">
        <span className={`cc-status-light ${visualState}`} />
        {visualState === "error" ? "Let’s try that again" : interactionState === "speaking" ? "Rocky is speaking" : currentState.detail}
        {interactionState === "speaking" && <button type="button" onClick={() => stopRockySpeech({ releasePlaybackContext: true })}>Stop <span aria-hidden="true">■</span></button>}
      </div>
      <form className="rocky-form" onSubmit={onSubmit}>
        <div className="rocky-input-shell">
          <textarea
            ref={inputRef}
            className="rocky-input"
            rows={1}
            value={draft}
            disabled={submitting || listening || micStarting}
            onChange={(event) => {
              setDraft(event.target.value);
              window.requestAnimationFrame(resizeComposer);
            }}
            onKeyDown={onComposerKeyDown}
            placeholder="Ask Rocky anything…"
            aria-label="Ask Rocky anything, or tell it what to do"
            aria-describedby="rocky-status-detail"
          />
          <button
            className="rocky-action"
            type={!listening && hasDraft ? "submit" : "button"}
            disabled={submitting || micStarting || (!hasDraft && !voiceSupported)}
            aria-label={
              micStarting
                ? "Starting microphone"
                : listening
                ? "Stop listening"
                : hasDraft
                  ? "Send message to Rocky"
                  : voiceSupported
                    ? "Use microphone"
                    : "Voice input is not available in this browser"
            }
            aria-pressed={!hasDraft ? listening : undefined}
            title={
              micStarting
                ? "Starting microphone"
                : listening
                ? "Stop listening"
                : hasDraft
                  ? "Send"
                  : voiceSupported
                    ? "Use microphone"
                    : "Voice input is not available in this browser"
            }
            onClick={listening ? stopListening : hasDraft ? undefined : startListening}
          >
            {submitting || micStarting || hasDraft || listening ? (
              <span>{submitting || micStarting ? "..." : listening ? "Stop" : "Send"}</span>
            ) : (
              <svg
                className="rocky-mic-icon"
                aria-hidden="true"
                viewBox="0 0 24 24"
              >
                <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z" />
                <path d="M19 11a7 7 0 0 1-14 0" />
                <path d="M12 18v3" />
                <path d="M8 21h8" />
              </svg>
            )}
          </button>
        </div>
      </form>

      {(!voiceSupported || !online || voiceError || error) && <div className="rocky-state" aria-live="polite">
        {!voiceSupported && <span>{voiceUnavailableMessage}</span>}
        {!online && <span className="err">Offline. Backend-dependent requests are paused.</span>}
        {voiceError && <span className="err">{voiceError}</span>}
        {error && <span className="err">{error}</span>}
      </div>}

      <div className="cc-command-tools">
      <nav className="cc-quick-actions" aria-label="Command shortcuts">
        <Link to="/projects"><span aria-hidden="true">□</span> Projects</Link>
        {["Remind me to ", "What's next?"].map((text) => <button key={text} type="button" disabled={submitting || listening || micStarting} onClick={() => { setDraft(text); inputRef.current?.focus(); window.requestAnimationFrame(resizeComposer); }}><span aria-hidden="true">{text.startsWith("Remind") ? "◷" : "↗"}</span>{text.startsWith("Remind") ? "Remind me" : text}</button>)}
      </nav>
      <div className="rocky-options">
        <label
          className="rocky-audio-toggle"
          title={spokenOutput ? "Spoken replies on" : "Spoken replies off"}
          aria-label="Spoken replies"
        >
          <input
            type="checkbox"
            checked={spokenOutput}
            onChange={(event) => {
              spokenOutputRef.current = event.target.checked;
              setSpokenOutput(event.target.checked);
              try {
                window.localStorage.setItem(SPOKEN_OUTPUT_KEY, String(event.target.checked));
              } catch {
                /* preference persistence is best-effort */
              }
              if (!event.target.checked) stopRockySpeech({ releasePlaybackContext: true });
            }}
          />
          <svg aria-hidden="true" viewBox="0 0 24 24">
            <path d="M4 10v4h4l5 4V6l-5 4H4Z" />
            <path d="M16 9a4 4 0 0 1 0 6" />
            <path d="M18.5 6.5a8 8 0 0 1 0 11" />
          </svg>
          <span>{spokenOutput ? "Audio on" : "Audio off"}</span>
        </label>
      </div>

      <aside className="cc-live" aria-label="Live intelligence">
        <div><span className="cc-overline">Live intelligence</span><p>Bring the world into context.</p></div>
        <div className="cc-live-actions">{["What's the weather today?", "What's the latest news?"].map((text) => <button type="button" key={text} disabled={submitting || listening || micStarting} onClick={() => { setDraft(text); inputRef.current?.focus(); window.requestAnimationFrame(resizeComposer); }}>{text.includes("weather") ? "Local weather ↗" : "Latest news ↗"}</button>)}</div>
        <small>Ask to check. Location is requested only when needed.</small>
      </aside>
      </div>
      {response && (
        <div className="rocky-latest" ref={latestResponseRef} aria-live="polite">
          <p className="rocky-latest-label">Rocky</p>
          <p className="rocky-latest-reply" aria-label="Rocky's latest response">
            {response.reply}
          </p>
          {spokenOutput && (
            <button
              className="rocky-replay"
              type="button"
              disabled={interactionState === "speaking"}
              onClick={() => {
                unlockAudioPlayback();
                void speak(response.reply, response.language);
              }}
            >
              Replay
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function isToday(value: string): boolean {
  const date = new Date(value);
  const now = new Date();
  return !Number.isNaN(date.getTime())
    && date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
}

function activeReminders(data: MissionControlData): Reminder[] {
  return data.reminders
    .filter((reminder) => reminder.status === "scheduled" || reminder.status === "due")
    .sort((a, b) => new Date(a.due_at).getTime() - new Date(b.due_at).getTime());
}

function dueReminders(data: MissionControlData): Reminder[] {
  return activeReminders(data).filter(
    (reminder) => reminder.status === "due" || isToday(reminder.due_at),
  );
}

function displayName(data: MissionControlData | null): string | null {
  const fullName = data?.profile?.full_name?.trim();
  if (!fullName) return null;
  const firstName = fullName.split(/\s+/)[0];
  return firstName.toLocaleLowerCase() === "rocky" ? null : firstName;
}

function overviewCopy(data: MissionControlData | null): string {
  if (!data) return "Bringing your day into focus.";
  const parts: string[] = [];
  if (!data.errors.work) {
    parts.push(`${data.activeTasks.length} open ${data.activeTasks.length === 1 ? "task" : "tasks"}`);
  }
  if (!data.errors.reminders) {
    const count = dueReminders(data).length;
    parts.push(`${count} ${count === 1 ? "reminder" : "reminders"} due today`);
  }
  if (!data.errors.work) {
    const count = data.projects.filter(({ project }) => project.status === "active").length;
    parts.push(`${count} active ${count === 1 ? "project" : "projects"}`);
  }
  return parts.length > 0 ? `You have ${parts.join(", ")}.` : "Ask Rocky what needs your attention.";
}

export default function MissionControlPage() {
  const { data, loading, error, reload } = useResource<MissionControlData>(loadMissionControl);

  return (
    <AppShell>
      <div className="rocky-home command-center">
        <header className="cc-topbar"><span>MISSION CONTROL <i /> Personal intelligence</span><Link to="/notifications" aria-label="Open notifications">Notifications <span aria-hidden="true">↗</span></Link></header>
        <RockyInteraction
          name={displayName(data)}
          summary={overviewCopy(data)}
          onMutatingAction={reload}
        />

        {loading && <div className="mc-loading" aria-label="Loading Mission Control" />}
        {error && <p className="err" role="alert">{error}</p>}

        {data && (
          <>
            <CommandCenterPanels data={data} />
          </>
        )}
      </div>
    </AppShell>
  );
}
