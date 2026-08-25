import { Link } from "react-router-dom";
import AppShell from "../AppShell";
import { listProjects, listTasks, updateTask } from "../api";
import { useResource } from "../useApi";
import type { Project, Task, TaskStatus } from "../types";
import { useState } from "react";

interface ProjectTask {
  project: Project;
  task: Task;
}

async function loadProjectTasks(): Promise<ProjectTask[]> {
  const projects = await listProjects();
  const taskLists = await Promise.all(projects.map((project) => listTasks(project.id)));
  return projects.flatMap((project, index) => (
    taskLists[index].map((task) => ({ project, task }))
  ));
}

export default function TasksPage() {
  const { data, loading, error, reload } = useResource<ProjectTask[]>(loadProjectTasks);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const tasks = data ?? [];
  const active = tasks.filter(({ task }) => task.status === "active");
  const complete = tasks.filter(({ task }) => task.status === "complete");

  const toggle = async (item: ProjectTask) => {
    const next: TaskStatus = item.task.status === "complete" ? "active" : "complete";
    setBusyId(item.task.id);
    setMutationError(null);
    try {
      await updateTask(item.project.id, item.task.id, { status: next });
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Task could not be updated.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <AppShell>
      <div className="page-stack">
        <header className="page-head page-head-split">
          <div>
            <p className="page-kicker">Work</p>
            <h1>Tasks</h1>
          </div>
          <span className="pill">{active.length} active</span>
        </header>

        {mutationError && <p className="err" role="alert">{mutationError}</p>}
        {loading && <div className="mc-loading" aria-label="Loading tasks" />}
        {error && <p className="err" role="alert">{error}</p>}

        {data && tasks.length === 0 ? (
          <section className="mc-panel">
            <div className="mc-empty-state">
              <span className="mc-empty-mark" aria-hidden="true">✓</span>
              <strong>No tasks yet</strong>
              <span>Create tasks inside a project to see them here.</span>
            </div>
          </section>
        ) : data && (
          <div className="capability-grid">
            <TaskSection title="Active" items={active} busyId={busyId} onToggle={toggle} />
            <TaskSection title="Completed" items={complete} busyId={busyId} onToggle={toggle} />
          </div>
        )}
      </div>
    </AppShell>
  );
}

function TaskSection({
  title,
  items,
  busyId,
  onToggle,
}: {
  title: string;
  items: ProjectTask[];
  busyId: string | null;
  onToggle: (item: ProjectTask) => void;
}) {
  return (
    <section className="mc-panel">
      <div className="mc-panel-head"><h2>{title}</h2></div>
      {items.length === 0 ? (
        <div className="mc-empty-state">
          <span className="mc-empty-mark" aria-hidden="true">✓</span>
          <strong>{title === "Active" ? "No active tasks" : "No completed tasks"}</strong>
          <span>Project tasks will collect here.</span>
        </div>
      ) : (
        <ul className="capability-list">
          {items.map((item) => (
            <li className={`capability-row task ${item.task.status}`} key={item.task.id}>
              <button
                className={item.task.status === "complete" ? "check checked" : "check"}
                disabled={busyId === item.task.id}
                onClick={() => onToggle(item)}
                aria-label={item.task.status === "complete" ? "Mark active" : "Mark complete"}
              >
                {item.task.status === "complete" ? "✓" : ""}
              </button>
              <Link className="row-copy" to={`/projects/${item.project.id}`}>
                <strong>{item.task.title}</strong>
                <span>{item.project.name}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
