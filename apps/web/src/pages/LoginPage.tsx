import { useState } from "react";
import { useAuth } from "../useAuth";
import { ApiError } from "../api";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-brand">Rocky</div>
        <h1 className="login-title">Sign in</h1>
        <input
          className="input"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
          placeholder="Email"
          autoComplete="username"
        />
        <input
          className="input"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
          placeholder="Password"
          autoComplete="current-password"
        />
        <button className="btn-primary btn-block" onClick={submit} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {error && <p className="err" role="alert">{error}</p>}
      </div>
    </div>
  );
}
