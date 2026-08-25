import AppShell from "../AppShell";

export default function SettingsPage() {
  return (
    <AppShell>
      <div className="page-stack">
        <header className="page-head">
          <div>
            <p className="page-kicker">Workspace</p>
            <h1>Settings</h1>
          </div>
        </header>

        <section className="mc-panel">
          <div className="mc-empty-state">
            <span className="mc-empty-mark" aria-hidden="true">⚙</span>
            <strong>Settings are not configurable yet</strong>
            <span>This page is reserved for real account and workspace controls when the backend exposes them.</span>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
