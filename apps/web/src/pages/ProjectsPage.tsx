import { useState } from "react";
import { Link } from "react-router-dom";
import AppShell from "../AppShell";
import { createProject, listProjects } from "../api";
import { useResource } from "../useApi";
import type { Project } from "../types";

export default function ProjectsPage() {
  const { data, loading, error, reload } = useResource<Project[]>(listProjects);
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
      <header className="page-head">
        <h1>Projects</h1>
      </header>

      <div className="create-row">
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
          placeholder="New project name"
        />
        <button className="btn-primary" onClick={submit} disabled={creating || !name.trim()}>
          {creating ? "Creating…" : "Create"}
        </button>
      </div>
      {createError && <p className="err" role="alert">{createError}</p>}

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="err" role="alert">{error}</p>}
      {data && data.length === 0 && <p className="muted">No projects yet.</p>}

      <ul className="rows">
        {data?.map((p) => (
          <li key={p.id} className="row">
            <Link to={`/projects/${p.id}`} className="row-main">
              <span className="row-title">{p.name}</span>
              {p.description && <span className="row-sub">{p.description}</span>}
            </Link>
            <span className="pill">{p.status}</span>
          </li>
        ))}
      </ul>
    </AppShell>
  );
}
