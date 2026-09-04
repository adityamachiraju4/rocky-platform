import { useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import AuthLayout from "../components/AuthLayout";
import PasswordField from "../components/PasswordField";
import { loginErrorMessage } from "../authMessages";
import { useAuth } from "../useAuth";
import { requestEmailVerification } from "../api";
import { apiErrorCode } from "../apiErrors";

interface LoginLocationState { email?: string; passwordReset?: boolean }

export default function LoginPage() {
  const { login } = useAuth();
  const location = useLocation();
  const state = location.state as LoginLocationState | null;
  const [email, setEmail] = useState(state?.email ?? "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [verificationRequired, setVerificationRequired] = useState(false);
  const [resendStatus, setResendStatus] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try { await login({ email: email.trim(), password }); }
    catch (caught) {
      setVerificationRequired(apiErrorCode(caught) === "EMAIL_NOT_VERIFIED");
      setError(loginErrorMessage(caught));
    }
    finally { setBusy(false); }
  };

  return <AuthLayout><form className="auth-form" onSubmit={submit} noValidate>
    <div className="auth-heading"><p className="auth-eyebrow">Welcome back</p><h1>Continue with Rocky.</h1><p>Sign in to pick up where you left off.</p></div>
    {state?.passwordReset && <div className="auth-notice success" role="status"><strong>Password updated.</strong><span>Sign in with your new password.</span></div>}
    <div className="auth-field-group"><label htmlFor="login-email">Email</label><div className="auth-field"><input id="login-email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" inputMode="email" type="email" required autoFocus /></div></div>
    <PasswordField id="login-password" label="Password" value={password} onChange={setPassword} autoComplete="current-password" />
    <div className="auth-between"><Link to="/forgot-password">Forgot password?</Link></div>
    {error && <p className="auth-error" role="alert">{error}</p>}
    {verificationRequired && <div className="auth-inline-action">
      <button type="button" disabled={!email.trim()} onClick={() => {
        setResendStatus(null);
        void requestEmailVerification(email.trim())
          .then(() => setResendStatus("If the account is eligible, a new verification link is on its way."))
          .catch(() => setResendStatus("The request couldn’t be completed. Please try again shortly."));
      }}>Resend verification email</button>
      {resendStatus && <span role="status">{resendStatus}</span>}
    </div>}
    <button className="auth-submit" type="submit" disabled={busy || !email.trim() || password.length < 8}><span>{busy ? "Signing in…" : "Sign in"}</span><span aria-hidden="true">→</span></button>
    <p className="auth-switch">New to Rocky? <Link to="/register">Create an account</Link></p>
  </form></AuthLayout>;
}
