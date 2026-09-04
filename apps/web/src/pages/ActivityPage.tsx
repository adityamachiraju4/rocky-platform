import AppShell from "../AppShell";
import { listActivity } from "../api";
import { useResource } from "../useApi";
import { humanizeEvent, summarizePayload } from "../activityLabels";
import type { Activity } from "../types";

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function ActivityPage() {
  const { data, loading, error } = useResource<Activity[]>(listActivity);

  return (
    <AppShell>
      <header className="page-head">
        <h1>Activity</h1>
      </header>

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="err" role="alert">{error}</p>}
      {data && data.length === 0 && <p className="muted">No activity yet.</p>}

      <ul className="ledger">
        {data?.map((a) => {
          const summary = summarizePayload(a.payload);
          return (
            <li key={a.id} className="ledger-row">
              <span className="ledger-label">{humanizeEvent(a.event_type)}</span>
              {summary && <span className="ledger-summary">{summary}</span>}
              <time className="ledger-time" dateTime={a.created_at}>{formatTime(a.created_at)}</time>
            </li>
          );
        })}
      </ul>
    </AppShell>
  );
}
