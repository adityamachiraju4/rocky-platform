import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { confirmPasswordReset, requestPasswordReset } from "../api";
import AuthLayout from "../components/AuthLayout";
import PasswordField from "../components/PasswordField";

export default function PasswordRecoveryPage({ reset = false }: { reset?: boolean }) {
  return reset ? <ResetPasswordForm /> : <ForgotPasswordForm />;
}

function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!email.trim() || busy) return;
    setBusy(true); setError(null);
    try { await requestPasswordReset(email.trim()); setSent(true); }
    catch { setError("Rocky couldn’t submit that request. Check your connection and try again."); }
    finally { setBusy(false); }
  };
  return <AuthLayout><form className="auth-form auth-static" onSubmit={submit}>
    <div className="auth-heading"><p className="auth-eyebrow">Account recovery</p><h1>Find your way back.</h1><p>Enter your account email. If it’s eligible, Rocky will send a time-limited reset link.</p></div>
    {sent ? <div className="auth-notice success" role="status"><strong>Check your email.</strong><span>If an eligible account exists, password reset instructions have been sent.</span></div> : <div className="auth-field-group"><label htmlFor="recovery-email">Email</label><div className="auth-field"><input id="recovery-email" type="email" inputMode="email" autoComplete="email" required autoFocus value={email} onChange={(event) => setEmail(event.target.value)} /></div></div>}
    {error && <p className="auth-error" role="alert">{error}</p>}
    {!sent && <button className="auth-submit" type="submit" disabled={busy || !email.trim()}><span>{busy ? "Sending…" : "Send reset instructions"}</span><span aria-hidden="true">→</span></button>}
    <p className="auth-switch"><Link to="/login">Return to sign in</Link></p>
  </form></AuthLayout>;
}

function ResetPasswordForm() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passwordError = submitted && password.length < 8 ? "Use at least 8 characters." : null;
  const confirmationError = submitted && confirmation !== password ? "Passwords don’t match." : null;
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setSubmitted(true);
    if (!token || password.length < 8 || password !== confirmation || busy) return;
    setBusy(true); setError(null);
    try {
      await confirmPasswordReset(token, password);
      navigate("/login", { replace: true, state: { passwordReset: true } });
    } catch { setError("This reset link is invalid or expired. Request a new one to continue."); }
    finally { setBusy(false); }
  };
  return <AuthLayout><form className="auth-form auth-static" onSubmit={submit} noValidate>
    <div className="auth-heading"><p className="auth-eyebrow">Choose a new password</p><h1>Reset your password.</h1><p>This will sign out other Rocky sessions so your account can continue securely.</p></div>
    {!token && <div className="auth-notice neutral"><strong>This reset link is incomplete.</strong><span>Request a new password reset email to continue.</span></div>}
    {token && <><PasswordField id="reset-password" label="New password" value={password} onChange={setPassword} autoComplete="new-password" error={passwordError} /><PasswordField id="reset-confirmation" label="Confirm new password" value={confirmation} onChange={setConfirmation} autoComplete="new-password" error={confirmationError} /></>}
    {error && <p className="auth-error" role="alert">{error}</p>}
    {token ? <button className="auth-submit" type="submit" disabled={busy}><span>{busy ? "Updating…" : "Update password"}</span><span aria-hidden="true">→</span></button> : <Link className="auth-submit link" to="/forgot-password"><span>Request a new link</span><span aria-hidden="true">→</span></Link>}
  </form></AuthLayout>;
}
