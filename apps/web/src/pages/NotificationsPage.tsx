import AppShell from "../AppShell";
import { listNotifications, updateNotification } from "../api";
import { relativeTime } from "../format";
import { useResource } from "../useApi";
import type { Notification } from "../types";
import { useState } from "react";

export default function NotificationsPage() {
  const { data, loading, error, reload } = useResource<Notification[]>(listNotifications);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const notifications = [...(data ?? [])].sort(
    (a, b) => Number(b.status === "unread") - Number(a.status === "unread")
      || new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );

  const update = async (notification: Notification, status: "read" | "dismissed") => {
    setBusyId(notification.id);
    setMutationError(null);
    try {
      await updateNotification(notification.id, { status });
      reload();
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "Notification could not be updated.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <AppShell>
      <div className="page-stack">
        <header className="page-head page-head-split">
          <div>
            <p className="page-kicker">Signals</p>
            <h1>Notifications</h1>
          </div>
          <span className="pill">{notifications.filter((item) => item.status === "unread").length} unread</span>
        </header>

        {mutationError && <p className="err" role="alert">{mutationError}</p>}
        {loading && <div className="mc-loading" aria-label="Loading notifications" />}
        {error && <p className="err" role="alert">{error}</p>}

        {data && notifications.length === 0 ? (
          <section className="mc-panel">
            <div className="mc-empty-state">
              <span className="mc-empty-mark" aria-hidden="true">○</span>
              <strong>You're caught up</strong>
              <span>New Rocky notifications will appear here.</span>
            </div>
          </section>
        ) : (
          <ul className="capability-list">
            {notifications.map((notification) => (
              <li className={`capability-row notification-row ${notification.status}`} key={notification.id}>
                <div className="row-copy">
                  <span className="mc-kicker">{notification.type}</span>
                  <strong>{notification.title}</strong>
                  <span>{notification.body}</span>
                  <time>{relativeTime(notification.created_at)} · {notification.status}</time>
                </div>
                {notification.status !== "dismissed" && (
                  <div className="row-actions">
                    {notification.status === "unread" && (
                      <button className="btn-ghost" disabled={busyId === notification.id} onClick={() => update(notification, "read")}>
                        Mark read
                      </button>
                    )}
                    <button className="btn-ghost danger" disabled={busyId === notification.id} onClick={() => update(notification, "dismissed")}>
                      Dismiss
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
