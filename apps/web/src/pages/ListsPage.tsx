import { useCallback, useRef, useState, type FormEvent } from "react";
import AppShell from "../AppShell";
import {
  createList,
  createListItem,
  listListItems,
  listLists,
  updateList,
  updateListItem,
} from "../api";
import { relativeTime } from "../format";
import { useResource } from "../useApi";
import type { RockyList, RockyListItem } from "../types";

type ListStatusFilter = "active" | "archived";
type ItemFilter = "all" | "active" | "complete";

export default function ListsPage() {
  const [filter, setFilter] = useState<ListStatusFilter>("active");
  const listsFetcher = useCallback(() => listLists(filter), [filter]);
  const { data, loading, error, reload } = useResource<RockyList[]>(listsFetcher);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newListTitle, setNewListTitle] = useState("");
  const [newItem, setNewItem] = useState("");
  const [itemFilter, setItemFilter] = useState<ItemFilter>("all");
  const [busy, setBusy] = useState(false);
  const [busyItemId, setBusyItemId] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const editorRef = useRef<HTMLElement | null>(null);

  const lists = data ?? [];
  const selected = lists.find((list) => list.id === selectedId) ?? null;
  const itemFetcher = useCallback(
    () => selectedId ? listListItems(selectedId, itemFilter === "all" ? undefined : itemFilter) : Promise.resolve([]),
    [selectedId, itemFilter],
  );
  const items = useResource<RockyListItem[]>(itemFetcher);
  const sortedItems = [...(items.data ?? [])].sort((a, b) => a.position - b.position);

  const selectList = (id: string) => {
    setSelectedId(id);
    if (window.matchMedia("(max-width: 767px)").matches) {
      requestAnimationFrame(() => editorRef.current?.scrollIntoView({ block: "start" }));
    }
  };

  const createNewList = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const title = newListTitle.trim();
    if (!title || busy) return;
    setBusy(true);
    setMutationError(null);
    try {
      const list = await createList({ title });
      setNewListTitle("");
      setFilter("active");
      selectList(list.id);
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "List could not be created.");
    } finally {
      setBusy(false);
    }
  };

  const addItem = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = newItem.trim();
    if (!selected || selected.status !== "active" || !content || busy) return;
    setBusy(true);
    setMutationError(null);
    try {
      await createListItem(selected.id, { content });
      setNewItem("");
      items.reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Item could not be added.");
    } finally {
      setBusy(false);
    }
  };

  const archiveSelected = async () => {
    if (!selected || selected.status !== "active" || busy) return;
    setBusy(true);
    setMutationError(null);
    try {
      await updateList(selected.id, { status: "archived" });
      setSelectedId(null);
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "List could not be archived.");
    } finally {
      setBusy(false);
    }
  };

  const completeItem = async (item: RockyListItem) => {
    if (!selected || item.status !== "active") return;
    setBusyItemId(item.id);
    setMutationError(null);
    try {
      await updateListItem(selected.id, item.id, { status: "complete" });
      items.reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Item could not be completed.");
    } finally {
      setBusyItemId(null);
    }
  };

  const renameItem = async (item: RockyListItem, content: string) => {
    const next = content.trim();
    if (!selected || item.status !== "active" || !next || next === item.content) return;
    setBusyItemId(item.id);
    setMutationError(null);
    try {
      await updateListItem(selected.id, item.id, { content: next });
      items.reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Item could not be updated.");
    } finally {
      setBusyItemId(null);
    }
  };

  return (
    <AppShell>
      <div className="page-stack">
        <header className="page-head page-head-split">
          <div>
            <p className="page-kicker">Collections</p>
            <h1>Lists</h1>
          </div>
          <div className="segmented" role="group" aria-label="List status">
            <button className={filter === "active" ? "active" : ""} onClick={() => setFilter("active")}>Active</button>
            <button className={filter === "archived" ? "active" : ""} onClick={() => setFilter("archived")}>Archived</button>
          </div>
        </header>

        <form className="create-row compact-create" onSubmit={createNewList}>
          <input
            className="input"
            value={newListTitle}
            onChange={(event) => setNewListTitle(event.target.value)}
            placeholder="New list title"
            aria-label="New list title"
          />
          <button className="btn-primary" disabled={busy || !newListTitle.trim()}>
            {busy ? "Creating..." : "Create"}
          </button>
        </form>
        {mutationError && <p className="err" role="alert">{mutationError}</p>}
        {loading && <div className="mc-loading" aria-label="Loading lists" />}
        {error && <p className="err" role="alert">{error}</p>}

        {data && (
          <div className="workspace-grid">
            <section className="mc-panel">
              <div className="mc-panel-head"><h2>{filter === "active" ? "Active lists" : "Archived lists"}</h2></div>
              {lists.length === 0 ? (
                <div className="mc-empty-state">
                  <span className="mc-empty-mark" aria-hidden="true">≡</span>
                  <strong>{filter === "active" ? "No active lists" : "No archived lists"}</strong>
                  <span>Simple ordered lists will appear here.</span>
                </div>
              ) : (
                <ul className="select-list">
                  {lists.map((list) => (
                    <li key={list.id}>
                      <button
                        className={selected?.id === list.id ? "select-row active" : "select-row"}
                        onClick={() => selectList(list.id)}
                      >
                        <strong>{list.title}</strong>
                        <span>{relativeTime(list.updated_at)} · {list.status}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="mc-panel editor-panel" ref={editorRef}>
              {selected ? (
                <>
                  <div className="page-head-split tight">
                    <div>
                      <h2 className="panel-title">{selected.title}</h2>
                      <p className="mc-panel-subtitle">{selected.status}</p>
                    </div>
                    {selected.status === "active" && (
                      <button className="btn-ghost danger" disabled={busy} onClick={archiveSelected}>Archive</button>
                    )}
                  </div>

                  {selected.status === "active" && (
                    <form className="create-row compact-create" onSubmit={addItem}>
                      <input
                        className="input"
                        value={newItem}
                        onChange={(event) => setNewItem(event.target.value)}
                        placeholder="Add list item"
                        aria-label="Add list item"
                      />
                      <button className="btn-primary" disabled={busy || !newItem.trim()}>
                        Add
                      </button>
                    </form>
                  )}

                  <div className="segmented inline" role="group" aria-label="Item status">
                    <button className={itemFilter === "all" ? "active" : ""} onClick={() => setItemFilter("all")}>All</button>
                    <button className={itemFilter === "active" ? "active" : ""} onClick={() => setItemFilter("active")}>Active</button>
                    <button className={itemFilter === "complete" ? "active" : ""} onClick={() => setItemFilter("complete")}>Complete</button>
                  </div>

                  {items.loading && <div className="mc-loading compact" aria-label="Loading list items" />}
                  {items.error && <p className="err" role="alert">{items.error}</p>}
                  {items.data && sortedItems.length === 0 ? (
                    <div className="mc-empty-state">
                      <span className="mc-empty-mark" aria-hidden="true">✓</span>
                      <strong>No items here</strong>
                      <span>Add an item to start this list.</span>
                    </div>
                  ) : (
                    <ul className="capability-list">
                      {sortedItems.map((item) => (
                        <ListItemRow
                          key={item.id}
                          item={item}
                          disabled={selected.status !== "active" || busyItemId === item.id}
                          onComplete={completeItem}
                          onRename={renameItem}
                        />
                      ))}
                    </ul>
                  )}
                </>
              ) : (
                <div className="mc-empty-state">
                  <span className="mc-empty-mark" aria-hidden="true">≡</span>
                  <strong>Select a list</strong>
                  <span>Create or open a list to manage items.</span>
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function ListItemRow({
  item,
  disabled,
  onComplete,
  onRename,
}: {
  item: RockyListItem;
  disabled: boolean;
  onComplete: (item: RockyListItem) => void;
  onRename: (item: RockyListItem, content: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.content);

  const finish = () => {
    setEditing(false);
    onRename(item, draft);
  };

  return (
    <li className={`capability-row list-item ${item.status}`}>
      <button
        className={item.status === "complete" ? "check checked" : "check"}
        disabled={disabled || item.status === "complete"}
        onClick={() => onComplete(item)}
        aria-label={item.status === "complete" ? "Completed" : "Complete item"}
      >
        {item.status === "complete" ? "✓" : ""}
      </button>
      {editing && item.status === "active" ? (
        <input
          className="input inline-edit"
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={finish}
          onKeyDown={(event) => {
            if (event.key === "Enter") finish();
            if (event.key === "Escape") {
              setDraft(item.content);
              setEditing(false);
            }
          }}
        />
      ) : (
        <button
          className="row-copy item-copy"
          disabled={disabled || item.status !== "active"}
          onClick={() => {
            setDraft(item.content);
            setEditing(true);
          }}
        >
          <strong>{item.content}</strong>
          <time>{item.status}</time>
        </button>
      )}
    </li>
  );
}
