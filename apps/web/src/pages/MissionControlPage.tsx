import { Link } from "react-router-dom";
import AppShell from "../AppShell";
import { useResource } from "../useApi";
import { loadMissionControl, type MissionControlData } from "../missionControl";
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
  });
}

// Resolve an activity event to the in-app route for its entity, when we can.
// task.* -> the owning project's detail page; project.* -> that project.
// Returns null when there is no sensible target (unknown entity type).
function entityLink(activity: Activity, projectIdByTaskId: Map<string, string>): string | null {
  if (activity.entity_type === "project") {
    return `/projects/${activity.entity_id}`;
  }
  if (activity.entity_type === "task") {
    const projectId = projectIdByTaskId.get(activity.entity_id);
    return projectId ? `/projects/${projectId}` : null;
  }
  return null;
}

function ResumeCard({ data }: { data: MissionControlData }) {
  const { latestActivity } = data;
  if (!latestActivity) return null;

  const projectIdByTaskId = new Map<string, string>();
  for (const ref of data.activeTasks) {
    projectIdByTaskId.set(ref.task.id, ref.projectId);
  }
  const href = entityLink(latestActivity, projectIdByTaskId);
  const label = humanizeEvent(latestActivity.event_type);

  const inner = (
    <>
      <span className="resume-label">{label}</span>
      <span className="resume-time mono">{formatTime(latestActivity.created_at)}</span>
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
                      <span className="mc-count mono">{s.activeTaskCount}</span>
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
