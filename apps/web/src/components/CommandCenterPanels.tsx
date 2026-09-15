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
function Heading({ title, to }: { title: string; to?: string }) {
  return <header className="cc-panel-heading"><h2>{title}</h2>{to && <Link to={to}>View all <span aria-hidden="true">↗</span></Link>}</header>;
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
      <Heading title="Current focus" />
      <p className="cc-overline">Continue where you left off</p>
      {data.errors.work ? <Empty>Projects and tasks are temporarily unavailable.</Empty> : focus ? <>
        <span className="cc-focus-symbol" aria-hidden="true">↗</span>
        <h3>{focus.projectName}</h3>
        <p className="cc-focus-task">{focus.task.title}</p>
        <p className="cc-meta">{recent ? `Last activity · ${when(recent.created_at)}` : "An open task to continue"}</p>
        <Link className="cc-continue" to={`/projects/${focus.projectId}`}>Continue <span aria-hidden="true">→</span></Link>
      </> : <><h3>Room for your next idea.</h3><Empty>Your next open task will appear here.</Empty><Link className="cc-continue" to="/projects">Explore projects <span aria-hidden="true">→</span></Link></>}
    </section>

    <section className="cc-panel cc-today">
      <Heading title="Today" to="/reminders" />
      <p className="cc-overline">A little perspective on your day</p>
      <ol className="cc-timeline">
        <li className="cc-time-complete"><span className="cc-time-node" /><h3>Completed</h3><p>{data.errors.activity ? "Activity unavailable" : completed.length ? `${completed.length} recent completion${completed.length === 1 ? "" : "s"}` : "No completions in recent activity"}</p><small>Today’s recent activity</small></li>
        <li className="cc-time-current"><span className="cc-time-node" /><h3>Current</h3><p>{data.errors.reminders ? "Reminders unavailable" : due[0]?.title ?? "No overdue reminders"}</p><small>{!data.errors.reminders && due[0] ? when(due[0].due_at) : "What needs attention now"}</small></li>
        <li><span className="cc-time-node" /><h3>Upcoming</h3><p>{data.errors.reminders ? "Reminders unavailable" : upcoming[0]?.title ?? "Nothing else scheduled today"}</p><small>{!data.errors.reminders && upcoming[0] ? when(upcoming[0].due_at) : "Later today"}</small></li>
        <li><span className="cc-time-node" /><h3>Later</h3><p>{data.errors.reminders ? "Reminders unavailable" : later[0]?.title ?? "Space for what’s next"}</p><small>{!data.errors.reminders && later[0] ? when(later[0].due_at) : "Beyond today"}</small></li>
      </ol>
      {!data.errors.work && <Link className="cc-subtle-link" to="/tasks">{data.activeTasks.length} open tasks across your projects <span aria-hidden="true">↗</span></Link>}
    </section>

    <section className="cc-panel cc-noticed">
      <Heading title="Rocky noticed" to="/notifications" />
      <p className="cc-overline">From your workspace</p>
      <ul className="cc-observations">
        {!data.errors.reminders && reminders[0] && <li><span aria-hidden="true">◷</span><Link to="/reminders"><strong>{reminders[0].title}</strong><small>{due.includes(reminders[0]) ? "Due" : "Reminder"} · {when(reminders[0].due_at)}</small></Link></li>}
        {!data.errors.work && focus && <li><span aria-hidden="true">↗</span><Link to={`/projects/${focus.projectId}`}><strong>{focus.task.title}</strong><small>Still open in {focus.projectName}</small></Link></li>}
        {!data.errors.notifications && notifications.map((item) => <li key={item.id}><span aria-hidden="true">✧</span><Link to="/notifications"><strong>{item.title}</strong><p>{item.body}</p><small>{when(item.created_at)}</small></Link></li>)}
      </ul>
      {!hasObservations && <Empty>{data.errors.work || data.errors.reminders || data.errors.notifications ? "Some workspace signals are unavailable. Try refreshing." : "Nothing to call out yet. Your real work and reminders will guide what appears here."}</Empty>}
    </section>

    <section className="cc-panel cc-projects">
      <Heading title="Your projects" to="/projects" />
      {data.errors.work ? <Empty>Project details are temporarily unavailable.</Empty> : projects.length ? <div className="cc-project-list">{projects.map(({ project, activeTaskCount }) => {
        const next = data.activeTasks.find((item) => item.projectId === project.id);
        return <Link className="cc-project" key={project.id} to={`/projects/${project.id}`}>
          <div><span className="cc-project-dot" /><strong>{project.name}</strong><span className="cc-project-status">{project.status}</span></div>
          <p>{next ? next.task.title : "No open tasks"}</p>
          <small>{activeTaskCount} open {activeTaskCount === 1 ? "task" : "tasks"} · Updated {when(project.updated_at)}</small>
        </Link>;
      })}</div> : <Empty>No projects yet. Start a project to give your work a place to continue.</Empty>}
    </section>

    <section className="cc-panel cc-activity">
      <Heading title="Recent activity" to="/activity" />
      {data.errors.activity ? <Empty>Recent activity is temporarily unavailable.</Empty> : data.recentActivity.length ? <ul className="cc-activity-list">{data.recentActivity.slice(0, 4).map((item) => {
        const ref = data.entityById.get(item.entity_id);
        const body = <><span className="cc-activity-dot" /><span><strong>{ref?.name ?? humanizeEvent(item.event_type)}</strong><small>{humanizeEvent(item.event_type)} · {when(item.created_at)}</small></span></>;
        return <li key={item.id}>{ref ? <Link to={`/projects/${ref.projectId}`}>{body}</Link> : <div>{body}</div>}</li>;
      })}</ul> : <Empty>Your workspace activity will appear here as you make progress.</Empty>}
    </section>
  </div>;
}
