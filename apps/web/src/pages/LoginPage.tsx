import { useState } from "react";
import { useAuth } from "../useAuth";
import { ApiError } from "../api";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await login({ email, password });
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError("Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-split">
      <aside className="login-aside">
        <div className="login-mark">
          <svg
            className="login-logo"
            viewBox="0 0 24 24"
            aria-hidden="true"
            fill="none"
          >
            <path d="M4 20 L10 6 L12 11 L14 6 L20 20 Z" fill="var(--accent)" />
          </svg>
          <span className="login-word">Rocky</span>
        </div>
        <div className="login-aside-body">
          <h2 className="login-tagline">Your personal intelligence OS.</h2>
          <p className="login-blurb">
            Projects, tasks, activity, and context — unified in one secure
            workspace.
          </p>
        </div>
        <div className="login-aside-foot">
          <div className="login-foot-strong">Private by design.</div>
          <div className="login-foot-dim">Local first. Cloud by choice.</div>
        </div>
      </aside>

      <main className="login-main">
        <div className="login-card">
          <h1 className="login-title">Welcome back</h1>
          <p className="login-subtitle">Sign in to your Rocky workspace</p>

          <label className="field-label" htmlFor="login-email">
            Email
          </label>
          <div className="field">
            <svg className="field-icon" viewBox="0 0 24 24" aria-hidden="true">
              <rect x="3" y="5" width="18" height="14" rx="2" />
              <path d="m4 7 8 6 8-6" />
            </svg>
            <input
              id="login-email"
              className="field-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submit();
              }}
              placeholder="you@example.com"
              autoComplete="username"
            />
          </div>

          <label className="field-label" htmlFor="login-password">
            Password
          </label>
          <div className="field">
            <svg className="field-icon" viewBox="0 0 24 24" aria-hidden="true">
              <rect x="5" y="11" width="14" height="9" rx="2" />
              <path d="M8 11V8a4 4 0 0 1 8 0v3" />
            </svg>
            <input
              id="login-password"
              className="field-input"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submit();
              }}
              placeholder="Enter your password"
              autoComplete="current-password"
            />
            <button
              type="button"
              className="field-affix"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
              tabIndex={-1}
            >
              {showPassword ? (
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 3l18 18" />
                  <path d="M10.6 10.6a2 2 0 0 0 2.8 2.8" />
                  <path d="M9.4 5.2A9 9 0 0 1 12 5c5 0 9 5 9 7a12 12 0 0 1-2.2 2.9" />
                  <path d="M6.2 6.2A12 12 0 0 0 3 12c0 2 4 7 9 7a9 9 0 0 0 3-.5" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 12s4-7 9-7 9 7 9 7-4 7-9 7-9-7-9-7Z" />
                  <circle cx="12" cy="12" r="2.5" />
                </svg>
              )}
            </button>
          </div>

          <button
            className="btn-primary btn-block login-submit"
            onClick={submit}
            disabled={busy}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>

          {error && (
            <p className="err" role="alert">
              {error}
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
