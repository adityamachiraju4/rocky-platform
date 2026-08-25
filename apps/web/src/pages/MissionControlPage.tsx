import { Link } from "react-router-dom";
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import AppShell from "../AppShell";
import { ApiError, AuthExpiredError, sendConversation, synthesizeSpeech, transcribeAudio } from "../api";
import { useResource } from "../useApi";
import { loadMissionControl, type MissionControlData, type EntityRef } from "../missionControl";
import { humanizeEvent } from "../activityLabels";
import type { ConversationResponse, Reminder } from "../types";

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
  typeof window !== "undefined" && window.location.hostname === "localhost";
const RECORDING_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
];
const SPEECH_LEVEL_THRESHOLD = 0.035;
const SILENCE_STOP_MS = 900;
const MIN_RECORDING_MS = 700;
const MAX_RECORDING_MS = 30_000;

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
  return "rocky-voice.webm";
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
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [spokenOutput, setSpokenOutput] = useState(true);
  const [interactionState, setInteractionState] = useState<InteractionState>("idle");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const voiceAudioContextRef = useRef<AudioContext | null>(null);
  const voiceFrameRef = useRef<number | null>(null);
  const maxRecordingTimerRef = useRef<number | null>(null);
  const recordingStartedAtRef = useRef(0);
  const recordingStopRequestedAtRef = useRef<number | null>(null);
  const voiceRoundTripStartedAtRef = useRef<number | null>(null);
  const speechDetectedRef = useRef(false);
  const silenceStartedAtRef = useRef<number | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const speechAbortRef = useRef<AbortController | null>(null);
  const speechSupported = typeof window !== "undefined" && "speechSynthesis" in window;
  const recognitionSupported = typeof window !== "undefined" && speechRecognitionCtor() !== null;
  const recordingSupported = typeof navigator !== "undefined"
    && !!navigator.mediaDevices?.getUserMedia
    && mediaRecorderCtor() !== null;
  const voiceSupported = recordingSupported || recognitionSupported;
  const currentState = stateCopy(interactionState);
  const hasDraft = draft.trim().length > 0;

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
    speechAbortRef.current?.abort();
    speechAbortRef.current = null;
    if (audioSourceRef.current) {
      audioSourceRef.current.onended = null;
      try {
        audioSourceRef.current.stop();
      } catch {
        /* source may already be stopped */
      }
      audioSourceRef.current = null;
    }
    window.speechSynthesis?.cancel();
    setInteractionState((state) => (state === "speaking" ? "idle" : state));
  }, []);

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.stop();
      }
      cleanupRecording();
      stopRockySpeech();
    };
  }, [cleanupRecording, stopRockySpeech]);

  const speakBrowser = (reply: string) => {
    if (!speechSupported) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(reply);
    utterance.onend = () => setInteractionState("idle");
    utterance.onerror = () => setInteractionState("idle");
    setInteractionState("speaking");
    window.speechSynthesis.speak(utterance);
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
  ) => {
    const ctx = await ensureAudioContext();
    if (controller.signal.aborted) return;
    const buffer = await audio.arrayBuffer();
    if (controller.signal.aborted) return;
    const decoded = await ctx.decodeAudioData(buffer);
    if (controller.signal.aborted) return;

    const source = ctx.createBufferSource();
    source.buffer = decoded;
    source.connect(ctx.destination);
    audioSourceRef.current = source;
    setInteractionState("speaking");
    source.onended = () => {
      if (audioSourceRef.current === source) {
        audioSourceRef.current = null;
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

  const speak = async (reply: string) => {
    if (!spokenOutput) return;
    stopRockySpeech();
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
      const response = await synthesizeSpeech({ text: reply }, controller.signal);
      if (controller.signal.aborted) return;
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
      await playNeuralSpeech(response.blob, controller);
    } catch (e: unknown) {
      if (controller.signal.aborted) return;
      speechDebug("neural playback failed", {
        name: e instanceof Error ? e.name : typeof e,
        message: e instanceof Error ? e.message : String(e),
      });
      stopRockySpeech();
      speakBrowser(reply);
      if (speechSupported) {
        setVoiceError(
          e instanceof ApiError
            ? "Neural speech is unavailable; using browser voice."
            : "Unable to play Rocky's neural voice; using browser voice.",
        );
      } else {
        setVoiceError(
          e instanceof Error ? e.message : "Rocky could not speak the reply.",
        );
      }
    }
  };

  const submitMessage = async (message: string) => {
    const text = message.trim();
    if (!text || submitting) return;
    stopRockySpeech();
    setSubmitting(true);
    setInteractionState("thinking");
    setError(null);
    setVoiceError(null);
    setDraft("");
    const conversationStartedAt = performance.now();
    speechDebug("conversation request started");

    try {
      const result = await sendConversation({
        message: text,
        timezone: browserTimezone(),
      });
      speechDebug("conversation response", {
        elapsedMs: Math.round(performance.now() - conversationStartedAt),
      });
      setResponse(result);
      inputRef.current?.focus();
      setInteractionState("idle");
      void speak(result.reply);
      if (result.executed) {
        onMutatingAction();
      }
    } catch (e: unknown) {
      if (e instanceof AuthExpiredError) {
        setError("Your session expired. Please sign in again.");
      } else {
        setError(e instanceof Error ? e.message : "Rocky could not respond.");
      }
      setInteractionState("idle");
    } finally {
      setSubmitting(false);
    }
  };

  const submitTranscription = async (audio: Blob, filename: string) => {
    try {
      setSubmitting(true);
      setInteractionState("transcribing");
      setVoiceError(null);
      const transcriptionStartedAt = performance.now();
      speechDebug("transcription request started", { blobSize: audio.size });
      const result = await transcribeAudio(audio, filename);
      speechDebug("transcription response", {
        elapsedMs: Math.round(performance.now() - transcriptionStartedAt),
      });
      const text = result.text.trim();
      if (!text) {
        setVoiceError("I couldn't hear anything to send.");
        setInteractionState("idle");
        setSubmitting(false);
        return;
      }
      setSubmitting(false);
      await submitMessage(text);
    } catch (e: unknown) {
      if (e instanceof AuthExpiredError) {
        setError("Your session expired. Please sign in again.");
      } else if (e instanceof ApiError) {
        setVoiceError(e.message || "I couldn't transcribe that audio.");
      } else {
        setVoiceError(e instanceof Error ? e.message : "Recording could not be transcribed.");
      }
      setInteractionState("idle");
      setSubmitting(false);
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

  const startListening = async () => {
    unlockAudioPlayback();
    stopRockySpeech();
    if (recordingSupported) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mimeType = recordingMimeType();
        const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
        audioChunksRef.current = [];
        mediaStreamRef.current = stream;
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
          setVoiceError("Recording failed.");
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
          const type = mimeType || recorder.mimeType || "audio/webm";
          const audio = new Blob(chunks, { type });
          if (audio.size === 0) {
            setVoiceError("I couldn't hear anything to send.");
            setInteractionState("idle");
            return;
          }
          void submitTranscription(audio, recordingFilename(type));
        };
        recorder.start();
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
        if (e instanceof DOMException && e.name === "NotAllowedError") {
          setVoiceError("Microphone permission was denied.");
        } else if (e instanceof DOMException && e.name === "NotFoundError") {
          setVoiceError("No microphone was found.");
        } else {
          setVoiceError("Recording could not start.");
        }
        setInteractionState("idle");
        setListening(false);
        cleanupRecording();
      }
      return;
    }

    const Recognition = speechRecognitionCtor();
    if (!Recognition) {
      setVoiceError("Voice input is not available in this browser.");
      return;
    }

    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onstart = () => {
      setListening(true);
      setInteractionState("listening");
      setVoiceError(null);
    };
    recognition.onend = () => {
      setListening(false);
      setInteractionState((state) => (state === "listening" ? "idle" : state));
    };
    recognition.onerror = (event) => {
      setVoiceError(`Voice input stopped: ${event.error}.`);
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
    recognition.start();
  };

  const stopListening = () => {
    if (mediaRecorderRef.current?.state === "recording") {
      stopActiveRecorder("manual");
      return;
    }
    recognitionRef.current?.stop();
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
          <input
            ref={inputRef}
            className="rocky-input"
            value={draft}
            disabled={submitting}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask Rocky what changed, what matters today, or what to do next"
            aria-label="Ask Rocky what changed, what matters today, or what to do next"
            aria-describedby="rocky-status-detail"
          />
          <button
            className="rocky-action"
            type={hasDraft ? "submit" : "button"}
            disabled={submitting || (!hasDraft && !voiceSupported)}
            aria-label={
              hasDraft
                ? "Send message to Rocky"
                : voiceSupported
                  ? listening
                    ? "Stop listening"
                    : "Use microphone"
                  : "Voice input is not available in this browser"
            }
            aria-pressed={!hasDraft ? listening : undefined}
            title={
              hasDraft
                ? "Send"
                : voiceSupported
                  ? listening
                    ? "Stop listening"
                    : "Use microphone"
                  : "Voice input is not available in this browser"
            }
            onClick={hasDraft ? undefined : listening ? stopListening : startListening}
          >
            {submitting || hasDraft || listening ? (
              <span>{submitting ? "..." : hasDraft ? "Send" : "Stop"}</span>
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
        {!voiceSupported && <span>Voice input is unavailable in this browser.</span>}
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
              setSpokenOutput(event.target.checked);
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
        <div className="rocky-latest" aria-live="polite">
          <p className="rocky-latest-label">Rocky</p>
          <p className="rocky-latest-reply" aria-label="Rocky's latest response">
            {response.reply}
          </p>
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
    <section className="mc-summary" aria-label="Mission summary">
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
      <SectionHeading title="Resume" />
      <p className="mc-panel-subtitle">Continue where you left off</p>
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
    <section className="mc-panel mc-focus" aria-label="Today's Focus">
      <SectionHeading title="Today's Focus" />
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
