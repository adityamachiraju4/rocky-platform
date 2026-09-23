import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { MissionControlData } from "../missionControl";
import { humanizeEvent } from "../activityLabels";

function today(value: string, now: number) {
  return new Date(value).toDateString() === new Date(now).toDateString();
}
function when(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}
function Heading({ title, to, eyebrow }: { title: string; to?: string; eyebrow?: string }) {
  return <header className="cc-panel-heading"><div>{eyebrow && <p className="cc-overline">{eyebrow}</p>}<h2>{title}</h2></div>{to && <Link to={to}>View all <span aria-hidden="true">→</span></Link>}</header>;
}
function Empty({ children }: { children: string }) {
  return <p className="cc-empty">{children}</p>;
}

export default function CommandCenterPanels({ data }: { data: MissionControlData }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const recent = data.errors.activity ? undefined : data.recentActivity.find((activity) => {
    const ref = data.entityById.get(activity.entity_id);
    return ref && data.activeTasks.some((item) => item.projectId === ref.projectId);
  });
  const recentRef = recent ? data.entityById.get(recent.entity_id) : undefined;
  const focus = data.activeTasks.find((item) => item.projectId === recentRef?.projectId) ?? data.activeTasks[0];
  const reminders = data.reminders.filter((item) => item.status === "scheduled" || item.status === "due")
    .sort((a, b) => Date.parse(a.due_at) - Date.parse(b.due_at));
  const due = reminders.filter((item) => item.status === "due" || Date.parse(item.due_at) <= now);
  const upcoming = reminders.filter((item) => item.status !== "due" && Date.parse(item.due_at) > now && today(item.due_at, now));
  const later = reminders.filter((item) => item.status !== "due" && Date.parse(item.due_at) > now && !today(item.due_at, now));
  const completed = data.recentActivity.filter((item) => today(item.created_at, now) && /complet/.test(item.event_type));
  const projects = [...data.projects].sort((a, b) => Date.parse(b.project.updated_at) - Date.parse(a.project.updated_at)).slice(0, 4);
  const notifications = data.notifications.filter((item) => item.status === "unread").slice(0, 2);

  const hasObservations = (!data.errors.work && !!focus)
    || (!data.errors.reminders && reminders.length > 0)
    || (!data.errors.notifications && notifications.length > 0);

  return <div className="cc-dashboard">
    <section className="cc-panel cc-focus">
      <Heading title="Continue" eyebrow="Pick up where you left off" />
      {data.errors.work ? <Empty>Projects and tasks are temporarily unavailable.</Empty> : focus ? <>
        <div className="cc-focus-body">
          <span className="cc-focus-symbol" aria-hidden="true">↗</span>
          <div><p className="cc-meta">{focus.projectName}</p><h3>{focus.task.title}</h3><p className="cc-focus-context">{recent ? `Last touched ${when(recent.created_at)}` : "Ready for your next step"}</p></div>
          <Link className="cc-continue" to={`/projects/${focus.projectId}`}>Continue <span aria-hidden="true">→</span></Link>
        </div>
      </> : <div className="cc-focus-empty"><span className="cc-focus-symbol" aria-hidden="true">✦</span><div><h3>Your attention is open.</h3><p>Ask Rocky what to begin, or give your next idea a place to grow.</p></div><Link className="cc-continue" to="/projects">Start a project <span aria-hidden="true">→</span></Link></div>}
    </section>

    <section className="cc-panel cc-today">
      <Heading title="Today" to="/tasks" eyebrow="A clear view of your day" />
      <div className="cc-today-stats">
        <Link to="/tasks"><strong>{data.errors.work ? "—" : data.activeTasks.length}</strong><span>Open tasks</span></Link>
        <Link to="/reminders"><strong>{data.errors.reminders ? "—" : due.length}</strong><span>Due now</span></Link>
        <Link to="/reminders"><strong>{data.errors.reminders ? "—" : upcoming.length}</strong><span>Coming up</span></Link>
        <Link to="/activity"><strong>{data.errors.activity ? "—" : completed.length}</strong><span>Completed</span></Link>
      </div>
      <div className="cc-agenda">
        <div><span className="cc-agenda-dot current" /><p><strong>Now</strong>{data.errors.reminders ? "Reminders unavailable" : due[0]?.title ?? "Nothing urgent needs your attention"}</p><small>{!data.errors.reminders && due[0] ? when(due[0].due_at) : "You’re clear"}</small></div>
        <div><span className="cc-agenda-dot" /><p><strong>Next</strong>{data.errors.reminders ? "Reminders unavailable" : upcoming[0]?.title ?? later[0]?.title ?? "No upcoming reminders"}</p><small>{!data.errors.reminders && (upcoming[0] ?? later[0]) ? when((upcoming[0] ?? later[0]).due_at) : "Space for what matters"}</small></div>
      </div>
    </section>

    <section className="cc-panel cc-noticed">
      <Heading title="Rocky noticed" to="/notifications" eyebrow="Signals from your workspace" />
      <ul className="cc-observations">
        {!data.errors.reminders && reminders[0] && <li><span aria-hidden="true">◷</span><Link to="/reminders"><strong>{reminders[0].title}</strong><small>{due.includes(reminders[0]) ? "Due" : "Reminder"} · {when(reminders[0].due_at)}</small></Link></li>}
        {!data.errors.work && focus && <li><span aria-hidden="true">↗</span><Link to={`/projects/${focus.projectId}`}><strong>{focus.task.title}</strong><small>Still open in {focus.projectName}</small></Link></li>}
        {!data.errors.notifications && notifications.map((item) => <li key={item.id}><span aria-hidden="true">✧</span><Link to="/notifications"><strong>{item.title}</strong><p>{item.body}</p><small>{when(item.created_at)}</small></Link></li>)}
      </ul>
      {!hasObservations && <Empty>{data.errors.work || data.errors.reminders || data.errors.notifications ? "Some workspace signals are unavailable. Try refreshing." : "Rocky will surface timely reminders, unfinished work, and useful changes here."}</Empty>}
    </section>

    <section className="cc-panel cc-projects">
      <Heading title="Projects" to="/projects" eyebrow="Work in motion" />
      {data.errors.work ? <Empty>Project details are temporarily unavailable.</Empty> : projects.length ? <div className="cc-project-list">{projects.map(({ project, activeTaskCount }) => {
        const next = data.activeTasks.find((item) => item.projectId === project.id);
        return <Link className="cc-project" key={project.id} to={`/projects/${project.id}`}>
          <div><span className="cc-project-dot" /><strong>{project.name}</strong><span className="cc-project-status">{project.status}</span></div>
          <p>{next ? next.task.title : "No open tasks"}</p>
          <small>{activeTaskCount} open {activeTaskCount === 1 ? "task" : "tasks"} · Updated {when(project.updated_at)}</small>
        </Link>;
      })}</div> : <Empty>Projects you begin with Rocky will stay ready here.</Empty>}
    </section>

    <section className="cc-panel cc-activity">
      <Heading title="Recent activity" to="/activity" eyebrow="What changed" />
      {data.errors.activity ? <Empty>Recent activity is temporarily unavailable.</Empty> : data.recentActivity.length ? <ul className="cc-activity-list">{data.recentActivity.slice(0, 4).map((item) => {
        const ref = data.entityById.get(item.entity_id);
        const body = <><span className="cc-activity-dot" /><span><strong>{ref?.name ?? humanizeEvent(item.event_type)}</strong><small>{humanizeEvent(item.event_type)} · {when(item.created_at)}</small></span></>;
        return <li key={item.id}>{ref ? <Link to={`/projects/${ref.projectId}`}>{body}</Link> : <div>{body}</div>}</li>;
      })}</ul> : <Empty>Your progress will appear here as the workspace takes shape.</Empty>}
    </section>
  </div>;
}
