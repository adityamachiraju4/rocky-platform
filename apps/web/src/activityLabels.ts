// Humanize the frozen backend event_type vocabulary. Unknown types fall back to
// the raw string so the UI never hides a real backend event.

const LABELS: Record<string, string> = {
  "list.archived": "Archived list",
  "list.created": "Created list",
  "list.item_added": "Added list item",
  "list.item_completed": "Completed list item",
  "list.item_updated": "Updated list item",
  "note.archived": "Archived note",
  "note.created": "Created note",
  "note.updated": "Updated note",
  "project.created": "Created project",
  "project.updated": "Updated project",
  "reminder.cancelled": "Cancelled reminder",
  "reminder.completed": "Completed reminder",
  "reminder.created": "Created reminder",
  "reminder.due": "Reminder due",
  "task.created": "Created task",
  "task.updated": "Updated task",
  "task.completed": "Completed task",
};

export function humanizeEvent(eventType: string): string {
  return LABELS[eventType] ?? eventType;
}

export function summarizePayload(payload: Record<string, unknown>): string {
  const parts: string[] = [];
  const oldStatus = payload["old_status"];
  const newStatus = payload["new_status"];
  if (typeof oldStatus === "string" && typeof newStatus === "string") {
    parts.push(`${oldStatus} \u2192 ${newStatus}`);
  }
  const changed = payload["changed_fields"];
  if (Array.isArray(changed) && changed.length > 0) {
    parts.push(changed.map((c) => String(c)).join(", "));
  }
  return parts.join("  \u00b7  ");
}
