import { Link } from "react-router-dom";
import AppShell from "../AppShell";
import { useResource } from "../useApi";
import { loadMissionControl, type MissionControlData, type EntityRef } from "../missionControl";
import { humanizeEvent, summarizePayload } from "../activityLabels";

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

export default function MissionControlPage() {
  const { data, loading, error } = useResource<MissionControlData>(loadMissionControl);

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
