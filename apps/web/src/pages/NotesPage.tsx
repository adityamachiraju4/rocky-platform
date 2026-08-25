import { useCallback, useState, type FormEvent } from "react";
import AppShell from "../AppShell";
import { createNote, listNotes, updateNote } from "../api";
import { relativeTime } from "../format";
import { useResource } from "../useApi";
import type { Note } from "../types";

type NoteStatusFilter = "active" | "archived";

export default function NotesPage() {
  const [filter, setFilter] = useState<NoteStatusFilter>("active");
  const notesFetcher = useCallback(() => listNotes(filter), [filter]);
  const { data, loading, error, reload } = useResource<Note[]>(notesFetcher);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const [creatingTitle, setCreatingTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const notes = data ?? [];
  const selected = notes.find((note) => note.id === selectedId) ?? null;

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const title = creatingTitle.trim();
    if (!title || busy) return;
    setBusy(true);
    setMutationError(null);
    try {
      const note = await createNote({ title });
      setCreatingTitle("");
      setFilter("active");
      setSelectedId(note.id);
      setDraftTitle(note.title);
      setDraftContent(note.content);
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Note could not be created.");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!selected || selected.status !== "active" || busy) return;
    const title = draftTitle.trim();
    if (!title) return;
    setBusy(true);
    setMutationError(null);
    try {
      await updateNote(selected.id, { title, content: draftContent });
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Note could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  const archive = async () => {
    if (!selected || selected.status !== "active" || busy) return;
    setBusy(true);
    setMutationError(null);
    try {
      await updateNote(selected.id, { status: "archived" });
      setSelectedId(null);
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Note could not be archived.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="page-stack">
        <header className="page-head page-head-split">
          <div>
            <p className="page-kicker">Notes</p>
            <h1>Notes</h1>
          </div>
          <div className="segmented" role="group" aria-label="Note status">
            <button className={filter === "active" ? "active" : ""} onClick={() => setFilter("active")}>Active</button>
            <button className={filter === "archived" ? "active" : ""} onClick={() => setFilter("archived")}>Archived</button>
          </div>
        </header>

        <form className="create-row compact-create" onSubmit={create}>
          <input
            className="input"
            value={creatingTitle}
            onChange={(event) => setCreatingTitle(event.target.value)}
            placeholder="New note title"
            aria-label="New note title"
          />
          <button className="btn-primary" disabled={busy || !creatingTitle.trim()}>
            {busy ? "Creating..." : "Create"}
          </button>
        </form>
        {mutationError && <p className="err" role="alert">{mutationError}</p>}
        {loading && <div className="mc-loading" aria-label="Loading notes" />}
        {error && <p className="err" role="alert">{error}</p>}

        {data && (
          <div className="workspace-grid">
            <section className="mc-panel">
              <div className="mc-panel-head"><h2>{filter === "active" ? "Active notes" : "Archived notes"}</h2></div>
              {notes.length === 0 ? (
                <div className="mc-empty-state">
                  <span className="mc-empty-mark" aria-hidden="true">✎</span>
                  <strong>{filter === "active" ? "No active notes" : "No archived notes"}</strong>
                  <span>Plain text notes will appear here.</span>
                </div>
              ) : (
                <ul className="select-list">
                  {notes.map((note) => (
                    <li key={note.id}>
                      <button
                        className={selected?.id === note.id ? "select-row active" : "select-row"}
                        onClick={() => {
                          setSelectedId(note.id);
                          setDraftTitle(note.title);
                          setDraftContent(note.content);
                        }}
                      >
                        <strong>{note.title}</strong>
                        <span>{relativeTime(note.updated_at)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="mc-panel editor-panel">
              {selected ? (
                <>
                  <label className="field-label" htmlFor="note-title">Title</label>
                  <input
                    id="note-title"
                    className="input title-input"
                    value={draftTitle}
                    disabled={selected.status !== "active"}
                    onChange={(event) => setDraftTitle(event.target.value)}
                  />
                  <label className="field-label" htmlFor="note-content">Content</label>
                  <textarea
                    id="note-content"
                    className="input textarea note-textarea"
                    value={draftContent}
                    disabled={selected.status !== "active"}
                    onChange={(event) => setDraftContent(event.target.value)}
                    placeholder="Write the note..."
                  />
                  <div className="row-actions editor-actions">
                    <button className="btn-primary" disabled={busy || selected.status !== "active" || !draftTitle.trim()} onClick={save}>
                      {busy ? "Saving..." : "Save"}
                    </button>
                    <button className="btn-ghost danger" disabled={busy || selected.status !== "active"} onClick={archive}>
                      Archive
                    </button>
                  </div>
                </>
              ) : (
                <div className="mc-empty-state">
                  <span className="mc-empty-mark" aria-hidden="true">✎</span>
                  <strong>Select a note</strong>
                  <span>Create or open a note to edit it.</span>
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}
