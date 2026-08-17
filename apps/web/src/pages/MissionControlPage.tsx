import { Link } from "react-router-dom";
import { useEffect, useRef, useState, type FormEvent } from "react";
import AppShell from "../AppShell";
import { AuthExpiredError, sendConversation } from "../api";
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
};

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

function RockyInteraction({ onMutatingAction }: { onMutatingAction: () => void }) {
  const [draft, setDraft] = useState("");
  const [lastUserMessage, setLastUserMessage] = useState<string | null>(null);
  const [response, setResponse] = useState<ConversationResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [spokenOutput, setSpokenOutput] = useState(true);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const speechSupported = typeof window !== "undefined" && "speechSynthesis" in window;
  const recognitionSupported = typeof window !== "undefined" && speechRecognitionCtor() !== null;

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
      window.speechSynthesis?.cancel();
    };
  }, []);

  const speak = (reply: string) => {
    if (!spokenOutput || !speechSupported) return;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(reply));
  };

  const submitMessage = async (message: string) => {
    const text = message.trim();
    if (!text || submitting) return;
    setSubmitting(true);
    setError(null);
    setVoiceError(null);
    setLastUserMessage(text);

    try {
      const result = await sendConversation({
        message: text,
        timezone: browserTimezone(),
      });
      setResponse(result);
      speak(result.reply);
      if (result.executed && result.action === "task.update") {
        onMutatingAction();
      }
    } catch (e: unknown) {
      if (e instanceof AuthExpiredError) {
        setError("Your session expired. Please sign in again.");
      } else {
        setError(e instanceof Error ? e.message : "Rocky could not respond.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = draft;
    setDraft("");
    void submitMessage(text);
  };

  const startListening = () => {
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
      setVoiceError(null);
    };
    recognition.onend = () => setListening(false);
    recognition.onerror = (event) => {
      setVoiceError(`Voice input stopped: ${event.error}.`);
      setListening(false);
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
  };

  return (
    <section className="rocky-panel mc-section" aria-label="Rocky interaction">
      <div className="rocky-head">
        <h2 className="rocky-title">Rocky</h2>
        <label className="rocky-speech-toggle">
          <input
            type="checkbox"
            checked={spokenOutput}
            disabled={!speechSupported}
            onChange={(event) => setSpokenOutput(event.target.checked)}
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
