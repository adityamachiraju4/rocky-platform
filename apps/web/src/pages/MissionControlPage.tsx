import { Link } from "react-router-dom";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import AppShell from "../AppShell";
import { ApiError, AuthExpiredError, sendConversation, synthesizeSpeech, transcribeAudio } from "../api";
import { useResource } from "../useApi";
import { loadMissionControl, type MissionControlData, type EntityRef } from "../missionControl";
import { humanizeEvent } from "../activityLabels";
import type { ConversationResponse, Reminder } from "../types";
import { startBrowserSpeech, type BrowserSpeechStartResult } from "../browserSpeech";

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
  typeof window !== "undefined" && import.meta.env.DEV;
const RECORDING_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
];
const SPEECH_LEVEL_THRESHOLD = 0.035;
const SILENCE_STOP_MS = 900;
const MIN_RECORDING_MS = 700;
const MAX_RECORDING_MS = 30_000;
const SPOKEN_OUTPUT_KEY = "rocky.spoken-output";

// Resolve an activity event to the in-app route for its entity, when we can.
// task.* -> the owning project's detail page; project.* -> that project.
// Returns null when there is no sensible target (unknown entity type).
function entityLink(ref: EntityRef | undefined): string | null {
  if (!ref) return null;
  return `/projects/${ref.projectId}`;
}

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

function browserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
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

function speechDebug(message: string, metadata: Record<string, unknown> = {}) {
  if (SPEECH_DEBUG) {
    console.info(`[Rocky speech] ${message}`, metadata);
  }
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
  const audioChunksRef = useRef<Blob[]>([]);
  const voiceAudioContextRef = useRef<AudioContext | null>(null);
  const voiceSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const voiceAnalyserRef = useRef<AnalyserNode | null>(null);
  const voiceFrameRef = useRef<number | null>(null);
  const maxRecordingTimerRef = useRef<number | null>(null);
  const recordingStartedAtRef = useRef(0);
  const recordingStopRequestedAtRef = useRef<number | null>(null);
  const voiceRoundTripStartedAtRef = useRef<number | null>(null);
  const speechDetectedRef = useRef(false);
  const silenceStartedAtRef = useRef<number | null>(null);
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
    if (maxRecordingTimerRef.current !== null) {
      window.clearTimeout(maxRecordingTimerRef.current);
      maxRecordingTimerRef.current = null;
    }
    const context = voiceAudioContextRef.current;
    voiceAudioContextRef.current = null;
    voiceSourceRef.current?.disconnect();
    voiceSourceRef.current = null;
    voiceAnalyserRef.current?.disconnect();
    voiceAnalyserRef.current = null;
    if (context && context.state !== "closed") {
      void context.close();
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

  const stopRockySpeech = useCallback(() => {
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
    setInteractionState((state) => (state === "speaking" ? "idle" : state));
  }, []);

  const closePlaybackContext = useCallback(() => {
    const context = audioContextRef.current;
    audioContextRef.current = null;
    if (context && context.state !== "closed") void context.close();
  }, []);

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
      cleanupRecording();
      stopRockySpeech();
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
      cleanupRecording();
      stopRockySpeech();
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
    const speechStartedAt = performance.now();
    speechDebug("speech request started");
    try {
      const response = await synthesizeSpeech({ text: reply, language }, controller.signal);
      if (controller.signal.aborted || speechIdRef.current !== speechId) return;
      speechDebug("speech response", {
        elapsedMs: Math.round(performance.now() - speechStartedAt),
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
      if (controller.signal.aborted || speechIdRef.current !== speechId) return;
      speechDebug("neural playback failed", {
        name: e instanceof Error ? e.name : typeof e,
        message: e instanceof Error ? e.message : String(e),
      });
      stopRockySpeech();
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
  ) => {
    setInteractionState("thinking");
    const conversationStartedAt = performance.now();
    speechDebug("conversation request started");
    try {
      const result = await sendConversation({
        message: text,
        timezone: browserTimezone(),
        language,
      }, controller.signal);
      if (!isCurrentTurn(id, controller)) return;
      speechDebug("conversation response", {
        elapsedMs: Math.round(performance.now() - conversationStartedAt),
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
      if (!isCurrentTurn(id, controller)) return;
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
    const turn = beginTurn();
    if (!turn) return;
    stopRockySpeech();
    setError(null);
    setVoiceError(null);
    setDraft("");
    window.requestAnimationFrame(resizeComposer);
    const rect = inputRef.current?.getBoundingClientRect();
    followResponseRef.current = !rect || (rect.bottom >= 0 && rect.top <= window.innerHeight);
    await runConversation(text, turn.id, turn.controller);
  };

  const submitTranscription = async (audio: Blob, filename: string) => {
    const turn = beginTurn();
    if (!turn) return;
    try {
      setInteractionState("transcribing");
      setVoiceError(null);
      const transcriptionStartedAt = performance.now();
      speechDebug("transcription request started", { blobSize: audio.size });
      const result = await transcribeAudio(audio, filename, turn.controller.signal);
      if (!isCurrentTurn(turn.id, turn.controller)) return;
      speechDebug("transcription response", {
        elapsedMs: Math.round(performance.now() - transcriptionStartedAt),
      });
      const text = result.text.trim();
      if (!text) {
        setVoiceError("I couldn't hear anything to send.");
        setInteractionState("idle");
        return;
      }
      await runConversation(text, turn.id, turn.controller, result.language);
    } catch (e: unknown) {
      if (!isCurrentTurn(turn.id, turn.controller)) return;
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
      voiceRoundTripStartedAtRef.current = now;
      speechDebug("recording stop requested", {
        reason,
        silenceMs: silenceStartedAtRef.current === null
          ? undefined
          : Math.round(now - silenceStartedAtRef.current),
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

    const samples = new Uint8Array(analyser.fftSize);
    const tick = () => {
      if (recorder.state !== "recording") return;
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) {
        const centered = (sample - 128) / 128;
        sum += centered * centered;
      }
      const level = Math.sqrt(sum / samples.length);
      const now = performance.now();
      if (level >= SPEECH_LEVEL_THRESHOLD) {
        speechDetectedRef.current = true;
        silenceStartedAtRef.current = null;
      } else if (
        speechDetectedRef.current
        && now - recordingStartedAtRef.current >= MIN_RECORDING_MS
      ) {
        silenceStartedAtRef.current ??= now;
        if (now - silenceStartedAtRef.current >= SILENCE_STOP_MS) {
          stopActiveRecorder("silence");
          return;
        }
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

  const startListening = async () => {
    if (
      turnLockedRef.current
      || recordingStartingRef.current
      || mediaRecorderRef.current?.state === "recording"
      || recognitionRef.current
    ) return;
    recordingStartingRef.current = true;
    setMicStarting(true);
    unlockAudioPlayback();
    stopRockySpeech();
    if (recordingSupported) {
      let stream: MediaStream | null = null;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (!lifecycleActiveRef.current) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        mediaStreamRef.current = stream;
        const mimeType = recordingMimeType();
        let recorder: MediaRecorder;
        try {
          recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
        } catch (error) {
          if (!mimeType) throw error;
          recorder = new MediaRecorder(stream);
        }
        audioChunksRef.current = [];
        mediaRecorderRef.current = recorder;
        recordingStartedAtRef.current = performance.now();
        recordingStopRequestedAtRef.current = null;
        voiceRoundTripStartedAtRef.current = null;
        speechDetectedRef.current = false;
        silenceStartedAtRef.current = null;

        recorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            audioChunksRef.current.push(event.data);
          }
        };
        recorder.onerror = () => {
          recorder.onstop = null;
          setVoiceError("Recording failed.");
          audioChunksRef.current = [];
          recordingStartingRef.current = false;
          setListening(false);
          setInteractionState("idle");
          cleanupRecording();
        };
        recorder.onstop = () => {
          const blobReadyAt = performance.now();
          const chunks = audioChunksRef.current;
          const duration = blobReadyAt - recordingStartedAtRef.current;
          const speechDetected = speechDetectedRef.current;
          audioChunksRef.current = [];
          setListening(false);
          cleanupRecording();
          speechDebug("recording blob ready", {
            stopToBlobMs: recordingStopRequestedAtRef.current === null
              ? undefined
              : Math.round(blobReadyAt - recordingStopRequestedAtRef.current),
            recordingMs: Math.round(duration),
          });
          if (!speechDetected || duration < MIN_RECORDING_MS || chunks.length === 0) {
            setVoiceError("I couldn't hear anything to send.");
            setInteractionState("idle");
            return;
          }
          const type = recorder.mimeType || mimeType || "audio/webm";
          const audio = new Blob(chunks, { type });
          if (audio.size === 0) {
            setVoiceError("I couldn't hear anything to send.");
            setInteractionState("idle");
            return;
          }
          void submitTranscription(audio, recordingFilename(type));
        };
        recorder.start();
        recordingStartingRef.current = false;
        void startSilenceDetection(stream, recorder).catch(() => {
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
        <p className="mission-eyebrow">Mission Control</p>
        <h1 className="rocky-title">{name ? `${greeting()}, ${name}` : greeting()}</h1>
        <p className="rocky-question">{summary}</p>
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
            placeholder="Ask Rocky anything, or tell it what to do"
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

      <div className="rocky-state" aria-live="polite">
        <span className={`rocky-state-dot ${interactionState}`} aria-hidden="true" />
        <strong>{currentState.label}</strong>
        <span id="rocky-status-detail">{currentState.detail}</span>
        {interactionState === "speaking" && (
          <button className="rocky-stop" type="button" onClick={stopRockySpeech}>
            Stop speaking
          </button>
        )}
        {!voiceSupported && <span>{voiceUnavailableMessage}</span>}
        {!online && <span className="err">Offline. Backend-dependent requests are paused.</span>}
        {voiceError && <span className="err">{voiceError}</span>}
        {error && <span className="err">{error}</span>}
      </div>

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
              if (!event.target.checked) stopRockySpeech();
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

function relativeTime(value: string): string {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Recently";
  const difference = timestamp - Date.now();
  const absolute = Math.abs(difference);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (absolute < 60_000) return "Just now";
  if (absolute < 3_600_000) return formatter.format(Math.round(difference / 60_000), "minute");
  if (absolute < 86_400_000) return formatter.format(Math.round(difference / 3_600_000), "hour");
  if (absolute < 604_800_000) return formatter.format(Math.round(difference / 86_400_000), "day");
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function dueLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Due soon";
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (isToday(value)) return `Today, ${time}`;
  return date.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
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

function SectionHeading({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="mc-panel-head">
      <h2>{title}</h2>
      {action}
    </div>
  );
}

function SummaryCards({ data }: { data: MissionControlData }) {
  const activeProjects = data.projects.filter(({ project }) => project.status === "active").length;
  const unread = data.notifications.filter((notification) => notification.status === "unread").length;
  const cards = [
    { label: "Active Projects", icon: "□", value: activeProjects, detail: "Projects in motion", href: "/projects", error: data.errors.work },
    { label: "Open Tasks", icon: "✓", value: data.activeTasks.length, detail: "Across projects", error: data.errors.work },
    { label: "Due Reminders", icon: "◷", value: dueReminders(data).length, detail: "Due today", error: data.errors.reminders },
    { label: "Unread Notifications", icon: "○", value: unread, detail: "Needs review", href: "/notifications", error: data.errors.notifications },
  ];

  return (
    <section className="mc-summary" aria-label="Today at a glance">
      {cards.map((card) => {
        const content = (
          <>
            <span className="mc-summary-icon" aria-hidden="true">{card.icon}</span>
            <span className="mc-summary-label">{card.label}</span>
            <strong>{card.error ? "--" : card.value}</strong>
            <span className={card.error ? "mc-summary-detail err" : "mc-summary-detail"}>
              {card.error ? "Unavailable" : card.detail}
            </span>
          </>
        );
        return card.href ? (
          <Link className="mc-summary-card" to={card.href} key={card.label}>{content}</Link>
        ) : (
          <div className="mc-summary-card" key={card.label}>{content}</div>
        );
      })}
    </section>
  );
}

function ResumePanel({ data }: { data: MissionControlData }) {
  const recent = data.recentActivity.find((activity) => {
    const ref = data.entityById.get(activity.entity_id);
    return ref && data.activeTasks.some((item) => item.projectId === ref.projectId);
  });
  const recentRef = recent ? data.entityById.get(recent.entity_id) : undefined;
  const candidate = data.activeTasks.find((item) => item.projectId === recentRef?.projectId)
    ?? data.activeTasks[0];

  return (
    <section className="mc-panel mc-resume" aria-label="Resume">
      <SectionHeading title="Continue" />
      <p className="mc-panel-subtitle">Pick up where you left off</p>
      {data.errors.work ? (
        <p className="mc-panel-error">Projects and tasks are temporarily unavailable.</p>
      ) : candidate ? (
        <Link className="mc-resume-link" to={`/projects/${candidate.projectId}`}>
          <span className="mc-kicker">{candidate.projectName}</span>
          <strong>{candidate.task.title}</strong>
          <span>{recent ? `${humanizeEvent(recent.event_type)} ${relativeTime(recent.created_at)}` : "Next open task"}</span>
          <span className="mc-text-action">Open project <span aria-hidden="true">→</span></span>
        </Link>
      ) : (
        <div className="mc-empty-state">
          <span className="mc-empty-mark" aria-hidden="true">□</span>
          <strong>Nothing waiting to resume</strong>
          <span>Your active work will appear here.</span>
        </div>
      )}
    </section>
  );
}

function FocusPanel({ data }: { data: MissionControlData }) {
  const reminders = dueReminders(data).slice(0, 2);
  const taskLimit = Math.max(0, 4 - reminders.length);
  const tasks = data.activeTasks.slice(0, taskLimit);
  const empty = reminders.length === 0 && tasks.length === 0;

  return (
    <section className="mc-panel mc-focus" aria-label="Today">
      <SectionHeading title="Today" />
      {(data.errors.work || data.errors.reminders) && empty ? (
        <p className="mc-panel-error">Focus items are temporarily unavailable.</p>
      ) : empty ? (
        <div className="mc-empty-state">
          <span className="mc-empty-mark" aria-hidden="true">✓</span>
          <strong>Your day is clear</strong>
          <span>No active tasks or reminders are due today.</span>
        </div>
      ) : (
        <ul className="mc-compact-list">
          {reminders.map((reminder) => (
            <li key={reminder.id}>
              <span className="mc-row-mark reminder" aria-hidden="true">◷</span>
              <span className="mc-row-copy"><strong>{reminder.title}</strong><span>{dueLabel(reminder.due_at)}</span></span>
            </li>
          ))}
          {tasks.map((item) => (
            <li key={item.task.id}>
              <span className="mc-row-mark" aria-hidden="true">✓</span>
              <Link className="mc-row-copy" to={`/projects/${item.projectId}`}>
                <strong>{item.task.title}</strong><span>{item.projectName}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function NotificationsPanel({ data }: { data: MissionControlData }) {
  const notifications = data.notifications
    .filter((notification) => notification.status !== "dismissed")
    .sort((a, b) => Number(b.status === "unread") - Number(a.status === "unread")
      || new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, 4);

  return (
    <section className="mc-panel mc-notifications" aria-label="Notifications">
      <SectionHeading title="Notifications" />
      {data.errors.notifications ? (
        <p className="mc-panel-error">Notifications are temporarily unavailable.</p>
      ) : notifications.length === 0 ? (
        <div className="mc-empty-state"><span className="mc-empty-mark" aria-hidden="true">○</span><strong>You're caught up</strong><span>No unread notifications.</span></div>
      ) : (
        <ul className="mc-intel-list">
          {notifications.map((notification) => {
            const ref = notification.source_id ? data.entityById.get(notification.source_id) : undefined;
            const href = entityLink(ref);
            const content = <><strong>{notification.title}</strong><span>{notification.body}</span><time>{relativeTime(notification.created_at)}</time></>;
            return (
              <li className={notification.status === "unread" ? "unread" : ""} key={notification.id}>
                {href ? <Link to={href}>{content}</Link> : <div>{content}</div>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function RemindersPanel({ data }: { data: MissionControlData }) {
  const reminders = activeReminders(data).slice(0, 4);
  return (
    <section className="mc-panel mc-reminders" aria-label="Upcoming Reminders">
      <SectionHeading title="Upcoming Reminders" />
      {data.errors.reminders ? (
        <p className="mc-panel-error">Reminders are temporarily unavailable.</p>
      ) : reminders.length === 0 ? (
        <div className="mc-empty-state"><span className="mc-empty-mark attention" aria-hidden="true">◷</span><strong>Nothing scheduled</strong><span>Upcoming reminders will appear here.</span></div>
      ) : (
        <ul className="mc-intel-list">
          {reminders.map((reminder) => (
            <li key={reminder.id} className={reminder.status === "due" ? "unread" : ""}>
              <div><strong>{reminder.title}</strong><span>{reminder.notes || "Personal reminder"}</span><time>{dueLabel(reminder.due_at)}</time></div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function MissionDashboard({ data }: { data: MissionControlData }) {
  return (
    <div className="mc-dashboard">
      <FocusPanel data={data} />
      <ResumePanel data={data} />
      <SummaryCards data={data} />
      <RemindersPanel data={data} />
      <NotificationsPanel data={data} />
    </div>
  );
}

function RecentActivity({ data }: { data: MissionControlData }) {
  const changes = data.recentActivity.slice(0, 7);

  return (
    <section className="ambient-section recent-section" aria-labelledby="recent-title">
      <div className="ambient-section-head row">
        <h2 id="recent-title">Recent</h2>
        <Link to="/activity" className="ambient-link">View all</Link>
      </div>
      {data.errors.activity ? (
        <p className="mc-panel-error">Recent activity is temporarily unavailable.</p>
      ) : changes.length === 0 ? (
        <p className="muted recent-empty">No activity yet.</p>
      ) : (
        <ul className="recent-list">
          {changes.map((a) => {
            const ref = data.entityById.get(a.entity_id);
            const href = entityLink(ref);
            const row = (
              <>
                <span className="recent-dot" aria-hidden="true" />
                <span className="recent-time">{relativeTime(a.created_at)}</span>
                <span className="recent-label">{humanizeEvent(a.event_type)}</span>
                <span className="recent-entity">{ref?.name ?? humanizeEvent(a.event_type)}</span>
                <span className="recent-arrow" aria-hidden="true">›</span>
              </>
            );
            return (
              <li key={a.id} className="recent-row">
                {href ? (
                  <Link to={href} className="recent-row-link">{row}</Link>
                ) : (
                  <div className="recent-row-link recent-row-static">{row}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export default function MissionControlPage() {
  const { data, loading, error, reload } = useResource<MissionControlData>(loadMissionControl);

  return (
    <AppShell>
      <div className="rocky-home">
        <RockyInteraction
          name={displayName(data)}
          summary={overviewCopy(data)}
          onMutatingAction={reload}
        />

        {loading && <div className="mc-loading" aria-label="Loading Mission Control" />}
        {error && <p className="err" role="alert">{error}</p>}

        {data && (
          <>
            <MissionDashboard data={data} />
            <RecentActivity data={data} />
          </>
        )}
      </div>
    </AppShell>
  );
}
