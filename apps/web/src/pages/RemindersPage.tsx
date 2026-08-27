import { useState, type FormEvent } from "react";
import AppShell from "../AppShell";
import { cancelReminder, completeReminder, createReminder, listReminders } from "../api";
import { browserTimezone, formatDateTime, toDateTimeLocalValue } from "../format";
import { useResource } from "../useApi";
import type { Reminder } from "../types";

const defaultDueAt = () => toDateTimeLocalValue(new Date(Date.now() + 60 * 60_000));

export default function RemindersPage() {
  const { data, loading, error, reload } = useResource<Reminder[]>(listReminders);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [dueAt, setDueAt] = useState(defaultDueAt);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const reminders = [...(data ?? [])].sort(
    (a, b) => new Date(a.due_at).getTime() - new Date(b.due_at).getTime(),
  );
  const active = reminders.filter((reminder) => reminder.status === "scheduled" || reminder.status === "due");
  const completed = reminders.filter((reminder) => reminder.status === "completed" || reminder.status === "cancelled");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = title.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    setMutationError(null);
    try {
      await createReminder({
        title: trimmed,
        notes: notes.trim() || null,
        due_at: new Date(dueAt).toISOString(),
        timezone: browserTimezone(),
      });
      setTitle("");
      setNotes("");
      setDueAt(defaultDueAt());
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Reminder could not be created.");
    } finally {
      setCreating(false);
    }
  };

  const transition = async (reminder: Reminder, action: "complete" | "cancel") => {
    setBusyId(reminder.id);
    setMutationError(null);
    try {
      if (action === "complete") await completeReminder(reminder.id);
      else await cancelReminder(reminder.id);
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Reminder could not be updated.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <AppShell>
      <div className="page-stack">
        <header className="page-head page-head-split">
          <div>
            <p className="page-kicker">Time</p>
            <h1>Reminders</h1>
          </div>
          <span className="pill">{browserTimezone() ?? "Local time"}</span>
        </header>

        <form className="surface-form reminder-form" onSubmit={submit}>
          <label className="field-label" htmlFor="reminder-title">
            Reminder
            <input
              id="reminder-title"
              className="input"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="What should Rocky remind you about?"
            />
          </label>
          <div className="form-grid">
            <label className="field-label" htmlFor="reminder-notes">
              Notes
              <textarea
                id="reminder-notes"
                className="input textarea"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Optional context"
                rows={3}
              />
            </label>
            <label className="field-label" htmlFor="reminder-due">
              Due
              <input
                id="reminder-due"
                className="input"
                type="datetime-local"
                value={dueAt}
                onChange={(event) => setDueAt(event.target.value)}
              />
            </label>
          </div>
          <button className="btn-primary action-fit" disabled={creating || !title.trim() || !dueAt}>
            {creating ? "Creating..." : "Create reminder"}
          </button>
          {mutationError && <p className="err" role="alert">{mutationError}</p>}
        </form>

        {loading && <div className="mc-loading" aria-label="Loading reminders" />}
        {error && <p className="err" role="alert">{error}</p>}

        {data && (
          <div className="capability-grid">
            <ReminderSection
              title="Active"
              empty="Nothing scheduled"
              reminders={active}
              busyId={busyId}
              onTransition={transition}
            />
            <ReminderSection
              title="History"
              empty="Completed and cancelled reminders will appear here."
              reminders={completed}
              busyId={busyId}
              onTransition={transition}
              passive
            />
          </div>
        )}
      </div>
    </AppShell>
  );
}

function ReminderSection({
  title,
  empty,
  reminders,
  busyId,
  passive = false,
  onTransition,
}: {
  title: string;
  empty: string;
  reminders: Reminder[];
  busyId: string | null;
  passive?: boolean;
  onTransition: (reminder: Reminder, action: "complete" | "cancel") => void;
}) {
  return (
    <section className="mc-panel">
      <div className="mc-panel-head"><h2>{title}</h2></div>
      {reminders.length === 0 ? (
        <div className="mc-empty-state">
          <span className="mc-empty-mark attention" aria-hidden="true">◷</span>
          <strong>{empty}</strong>
          <span>Rocky will keep time-bound work here.</span>
        </div>
      ) : (
        <ul className="capability-list">
          {reminders.map((reminder) => (
            <li className={`capability-row ${reminder.status}`} key={reminder.id}>
              <div className="row-copy">
                <strong>{reminder.title}</strong>
                {reminder.notes && <span>{reminder.notes}</span>}
                <time>{formatDateTime(reminder.due_at)} · {reminder.status}</time>
              </div>
              {!passive && (
                <div className="row-actions">
                  <button className="btn-ghost" disabled={busyId === reminder.id} onClick={() => onTransition(reminder, "complete")}>
                    Complete
                  </button>
                  <button className="btn-ghost danger" disabled={busyId === reminder.id} onClick={() => onTransition(reminder, "cancel")}>
                    Cancel
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
