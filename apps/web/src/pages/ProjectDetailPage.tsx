import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import AppShell from "../AppShell";
import {
  createTask,
  getProject,
  listTasks,
  updateProject,
  updateTask,
} from "../api";
import { useResource } from "../useApi";
import type { Project, Task, TaskStatus } from "../types";

export default function ProjectDetailPage() {
  const { projectId } = useParams();
  const pid = projectId ?? "";

  const projectFetcher = useCallback(() => getProject(pid), [pid]);
  const tasksFetcher = useCallback(() => listTasks(pid), [pid]);

  const project = useResource<Project>(projectFetcher);
  const tasks = useResource<Task[]>(tasksFetcher);

  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [taskError, setTaskError] = useState<string | null>(null);

  const addTask = async () => {
    const trimmed = title.trim();
    if (!trimmed) return;
    setBusy(true);
    setTaskError(null);
    try {
      await createTask(pid, { title: trimmed });
      setTitle("");
      tasks.reload();
    } catch (e) {
      setTaskError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (task: Task) => {
    const next: TaskStatus = task.status === "complete" ? "active" : "complete";
    try {
      await updateTask(pid, task.id, { status: next });
      tasks.reload();
    } catch (e) {
      setTaskError(e instanceof Error ? e.message : "Update failed");
    }
  };

  const renameProject = async (name: string) => {
    try {
      await updateProject(pid, { name });
      project.reload();
    } catch (e) {
      setTaskError(e instanceof Error ? e.message : "Update failed");
    }
  };

  return (
    <AppShell>
      <header className="page-head">
        <Link to="/projects" className="back">← Projects</Link>
        {project.data && (
          <EditableTitle value={project.data.name} onSave={renameProject} />
        )}
        {project.data && <span className="pill">{project.data.status}</span>}
      </header>

      {project.error && <p className="err" role="alert">{project.error}</p>}

      <div className="create-row">
        <input
          className="input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void addTask(); }}
          placeholder="New task"
        />
        <button className="btn-primary" onClick={addTask} disabled={busy || !title.trim()}>
          {busy ? "Adding…" : "Add task"}
        </button>
      </div>
      {taskError && <p className="err" role="alert">{taskError}</p>}

      {tasks.loading && <p className="muted">Loading…</p>}
      {tasks.data && tasks.data.length === 0 && <p className="muted">No tasks yet.</p>}

      <ul className="rows">
        {tasks.data?.map((t) => (
          <li key={t.id} className={t.status === "complete" ? "row task done" : "row task"}>
            <button
              className={t.status === "complete" ? "check checked" : "check"}
              onClick={() => toggle(t)}
              aria-label={t.status === "complete" ? "Mark active" : "Mark complete"}
            >
              {t.status === "complete" ? "✓" : ""}
            </button>
            <span className="task-title">{t.title}</span>
          </li>
        ))}
      </ul>
    </AppShell>
  );
}

function EditableTitle({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  if (!editing) {
    return (
      <h1 className="editable" onClick={() => { setDraft(value); setEditing(true); }}>
        {value}
      </h1>
    );
  }
  return (
    <input
      className="input title-input"
      autoFocus
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => { setEditing(false); if (draft.trim() && draft !== value) onSave(draft.trim()); }}
      onKeyDown={(e) => {
        if (e.key === "Enter") { setEditing(false); if (draft.trim() && draft !== value) onSave(draft.trim()); }
        if (e.key === "Escape") setEditing(false);
      }}
    />
  );
}
