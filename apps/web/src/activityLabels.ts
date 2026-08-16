// Humanize the frozen backend event_type vocabulary. Unknown types fall back to
// the raw string so the UI never hides a real backend event.

const LABELS: Record<string, string> = {
  "project.created": "Created project",
  "project.updated": "Updated project",
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
