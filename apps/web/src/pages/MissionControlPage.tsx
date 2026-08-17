import { Link } from "react-router-dom";
import { useEffect, useRef, useState, type FormEvent } from "react";
import AppShell from "../AppShell";
import { ApiError, AuthExpiredError, sendConversation, synthesizeSpeech } from "../api";
import { useResource } from "../useApi";
import { loadMissionControl, type MissionControlData, type EntityRef } from "../missionControl";
import { humanizeEvent, summarizePayload } from "../activityLabels";
import type { ConversationResponse } from "../types";

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

type InteractionState = "idle" | "listening" | "thinking" | "speaking";

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

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Resolve an activity event to the in-app route for its entity, when we can.
// task.* -> the owning project's detail page; project.* -> that project.
// Returns null when there is no sensible target (unknown entity type).
function entityLink(ref: EntityRef | undefined): string | null {
  if (!ref) return null;
  return `/projects/${ref.projectId}`;
}

function ResumeCard({ data }: { data: MissionControlData }) {
  const { latestActivity } = data;
  if (!latestActivity) return null;

  const ref = data.entityById.get(latestActivity.entity_id);
  const href = entityLink(ref);
  const label = humanizeEvent(latestActivity.event_type);
  const headline = ref ? ref.name : label;
  const context = ref
    ? `${ref.projectName} \u00b7 ${formatTime(latestActivity.created_at)}`
    : formatTime(latestActivity.created_at);

  const inner = (
    <>
      <span className="resume-eyebrow">{label}</span>
      <span className="resume-headline">{headline}</span>
      <span className="resume-context mono">{context}</span>
    </>
  );

  return (
    <section className="mc-section">
      <h2 className="mc-heading">Continue where you left off</h2>
      {href ? (
        <Link to={href} className="resume-card">
          {inner}
        </Link>
      ) : (
        <div className="resume-card resume-card-static">{inner}</div>
      )}
    </section>
  );
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
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

function audioContextCtor(): typeof AudioContext | null {
  const w = window as SpeechWindow;
  return window.AudioContext ?? w.webkitAudioContext ?? null;
}

function speechDebug(message: string, metadata: Record<string, unknown> = {}) {
  if (SPEECH_DEBUG) {
    console.info(`[Rocky speech] ${message}`, metadata);
  }
}

function RockyInteraction({ onMutatingAction }: { onMutatingAction: () => void }) {
  const [draft, setDraft] = useState("");
  const [lastUserMessage, setLastUserMessage] = useState<string | null>(null);
  const [response, setResponse] = useState<ConversationResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [spokenOutput, setSpokenOutput] = useState(true);
  const [interactionState, setInteractionState] = useState<InteractionState>("idle");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const speechAbortRef = useRef<AbortController | null>(null);
  const speechSupported = typeof window !== "undefined" && "speechSynthesis" in window;
  const recognitionSupported = typeof window !== "undefined" && speechRecognitionCtor() !== null;

  const stopRockySpeech = () => {
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
  };

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
      stopRockySpeech();
    };
  }, []);

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
    try {
      const response = await synthesizeSpeech({ text: reply }, controller.signal);
      if (controller.signal.aborted) return;
      speechDebug("speech response", {
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
    setLastUserMessage(text);

    try {
      const result = await sendConversation({
        message: text,
        timezone: browserTimezone(),
      });
      setResponse(result);
      setInteractionState("idle");
      void speak(result.reply);
      if (result.executed && result.action === "task.update") {
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

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    unlockAudioPlayback();
    const text = draft;
    setDraft("");
    void submitMessage(text);
  };

  const startListening = () => {
    unlockAudioPlayback();
    stopRockySpeech();
    const Recognition = speechRecognitionCtor();
    if (!Recognition) {
      setVoiceError("Voice input is not supported in this browser.");
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
      setDraft(transcript);
      void submitMessage(transcript);
    };
    recognitionRef.current = recognition;
    recognition.start();
  };

  const stopListening = () => {
    recognitionRef.current?.stop();
    setListening(false);
    setInteractionState("idle");
  };

  return (
    <section className="rocky-panel mc-section" aria-label="Rocky interaction">
      <div className="rocky-head">
        <h2 className="rocky-title">Rocky</h2>
        <label className="rocky-speech-toggle">
          <input
            type="checkbox"
            checked={spokenOutput}
            onChange={(event) => {
              setSpokenOutput(event.target.checked);
              if (!event.target.checked) stopRockySpeech();
            }}
          />
          Speak replies
        </label>
      </div>

      <form className="rocky-form" onSubmit={onSubmit}>
        <input
          className="input rocky-input"
          value={draft}
          disabled={submitting}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask Rocky what changed, or tell it what to finish"
        />
        <button className="btn-primary rocky-submit" disabled={submitting || !draft.trim()}>
          {submitting ? "Thinking" : "Send"}
        </button>
        <button
          className="rocky-mic"
          type="button"
          disabled={submitting || !recognitionSupported}
          aria-pressed={listening}
          title={recognitionSupported ? "Use microphone" : "Voice input is not supported in this browser"}
          onClick={listening ? stopListening : startListening}
        >
          {listening ? "Stop" : "Mic"}
        </button>
      </form>

      <div className="rocky-state" aria-live="polite">
        {listening && <span>Listening...</span>}
        {interactionState === "thinking" && <span>Thinking...</span>}
        {interactionState === "speaking" && (
          <button className="rocky-stop" type="button" onClick={stopRockySpeech}>
            Stop speaking
          </button>
        )}
        {interactionState === "idle" && !voiceError && !error && (
          <span>Idle</span>
        )}
        {!recognitionSupported && <span>Voice input is unavailable in this browser.</span>}
        {voiceError && <span className="err">{voiceError}</span>}
        {error && <span className="err">{error}</span>}
      </div>

      {(lastUserMessage || response) && (
        <div className="rocky-turn">
          {lastUserMessage && (
            <p className="rocky-user">
              <span>You</span>
              {lastUserMessage}
            </p>
          )}
          {response && (
            <p className="rocky-reply">
              <span>Rocky</span>
              {response.reply}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export default function MissionControlPage() {
  const { data, loading, error, reload } = useResource<MissionControlData>(loadMissionControl);

  const isEmpty =
    !!data &&
    data.projects.length === 0 &&
    data.activeTasks.length === 0 &&
    data.recentActivity.length === 0;

  return (
    <AppShell>
      <header className="page-head">
        <h1>{greeting()}</h1>
      </header>

      <RockyInteraction onMutatingAction={reload} />

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="err" role="alert">{error}</p>}

      {isEmpty && (
        <div className="mc-empty">
          <p className="muted">Nothing here yet — Rocky is a clean slate.</p>
          <Link to="/projects" className="btn-primary mc-empty-cta">
            Create your first project
          </Link>
        </div>
      )}

      {data && !isEmpty && (
        <>
          <ResumeCard data={data} />

          <div className="mc-grid">
            <section className="mc-section">
              <h2 className="mc-heading">Projects</h2>
              {data.projects.length === 0 ? (
                <p className="muted">No projects yet.</p>
              ) : (
                <ul className="mc-list">
                  {data.projects.map((s) => (
                    <li key={s.project.id} className="mc-list-row">
                      <Link to={`/projects/${s.project.id}`} className="mc-list-main">
                        {s.project.name}
                      </Link>
                      <span className="mc-count"><span className="mono">{s.activeTaskCount}</span> active</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="mc-section">
              <h2 className="mc-heading">Active tasks</h2>
              {data.activeTasks.length === 0 ? (
                <p className="muted">No active tasks.</p>
              ) : (
                <ul className="mc-list">
                  {data.activeTasks.map((ref) => (
                    <li key={ref.task.id} className="mc-list-row">
                      <Link to={`/projects/${ref.projectId}`} className="mc-list-main">
                        <span className="mc-task-title">{ref.task.title}</span>
                        <span className="mc-task-project">{ref.projectName}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <section className="mc-section">
            <h2 className="mc-heading">Recent activity</h2>
            {data.recentActivity.length === 0 ? (
              <p className="muted">No activity yet.</p>
            ) : (
              <ul className="ledger">
                {data.recentActivity.map((a) => {
                  const summary = summarizePayload(a.payload);
                  return (
                    <li key={a.id} className="ledger-row">
                      <span className="ledger-time mono">{formatTime(a.created_at)}</span>
                      <span className="ledger-label">{humanizeEvent(a.event_type)}</span>
                      <span className="ledger-type mono">{a.event_type}</span>
                      {summary && <span className="ledger-summary mono">{summary}</span>}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </>
      )}
    </AppShell>
  );
}
