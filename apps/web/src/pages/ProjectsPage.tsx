import { useState } from "react";
import { Link } from "react-router-dom";
import AppShell from "../AppShell";
import { createProject, listProjects, listTasks } from "../api";
import { useResource } from "../useApi";
import type { Project, Task } from "../types";

interface ProjectOverview { project: Project; tasks: Task[] }

async function loadProjects(): Promise<ProjectOverview[]> {
  const projects = await listProjects();
  const taskLists = await Promise.all(projects.map((project) => listTasks(project.id)));
  return projects.map((project, index) => ({ project, tasks: taskLists[index] }));
}

function relativeUpdate(value: string): string {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Updated recently";
  const days = Math.floor((Date.now() - timestamp) / 86_400_000);
  if (days <= 0) return "Updated today";
  if (days === 1) return "Updated yesterday";
  if (days < 7) return `Updated ${days} days ago`;
  return `Updated ${new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

export default function ProjectsPage() {
  const { data, loading, error, reload } = useResource<ProjectOverview[]>(loadProjects);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    setCreateError(null);
    try {
      await createProject({ name: trimmed });
      setName("");
      reload();
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setCreating(false);
    }
  };

  return (
    <AppShell>
      <header className="page-head page-head-split">
        <div><p className="page-kicker">In motion</p><h1>Projects</h1></div>
        <span className="pill">{data?.filter(({ project }) => project.status === "active").length ?? 0} active</span>
      </header>

      <div className="create-row">
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
          placeholder="New project name"
          aria-label="New project name"
        />
        <button className="btn-primary" onClick={submit} disabled={creating || !name.trim()}>
          {creating ? "Creating…" : "Create"}
        </button>
      </div>
      {createError && <p className="err" role="alert">{createError}</p>}

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="err" role="alert">{error}</p>}
      {data && data.length === 0 && <p className="muted">No projects yet.</p>}

      <ul className="rows project-rows">
        {data?.map(({ project, tasks }) => {
          const next = tasks.find((task) => task.status === "active");
          return <li key={project.id} className="row project-row">
            <Link to={`/projects/${project.id}`} className="row-main">
              <span className="row-title">{project.name}</span>
              <span className="row-sub">{next ? `Next · ${next.title}` : project.description || "No open tasks"}</span>
              <span className="project-updated">{relativeUpdate(project.updated_at)}</span>
            </Link>
            <span className="pill">{project.status}</span>
          </li>;
        })}
      </ul>
    </AppShell>
  );
}
