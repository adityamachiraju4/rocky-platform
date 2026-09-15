import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { register } from "../api";
import { registrationErrorMessage } from "../authMessages";
import { apiErrorCode } from "../apiErrors";
import AuthLayout from "../components/AuthLayout";
import PasswordField from "../components/PasswordField";
import { browserTimezone } from "../format";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function RegisterPage() {
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const emailError = submitted && !EMAIL_PATTERN.test(email.trim()) ? "Enter a valid email address." : null;
  const passwordError = submitted && password.length < 8 ? "Use at least 8 characters." : null;
  const confirmationError = submitted && confirmation !== password ? "Passwords don’t match." : null;
  const nameError = submitted && !fullName.trim() ? "Enter your name." : null;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setSubmitted(true);
    if (busy || !fullName.trim() || !EMAIL_PATTERN.test(email.trim()) || password.length < 8 || password !== confirmation) return;
    setBusy(true); setError(null);
    try {
      await register({ full_name: fullName.trim(), email: email.trim(), password, timezone: browserTimezone() || "UTC" });
      navigate("/verify-email", { replace: true, state: { email: email.trim(), sent: true } });
    } catch (caught) {
      if (apiErrorCode(caught) === "VERIFICATION_DELIVERY_FAILED") {
        navigate("/verify-email", { replace: true, state: { email: email.trim(), sent: false } });
        return;
      }
      setError(registrationErrorMessage(caught));
    }
    finally { setBusy(false); }
  };

  return <AuthLayout><form className="auth-form" onSubmit={submit} noValidate>
    <div className="auth-heading"><p className="auth-eyebrow">Begin with Rocky</p><h1>Create your workspace.</h1><p>A personal system for the work and context you want to keep connected.</p></div>
    <div className="auth-field-group"><label htmlFor="register-name">Full name</label><div className={nameError ? "auth-field invalid" : "auth-field"}><input id="register-name" value={fullName} onChange={(event) => setFullName(event.target.value)} autoComplete="name" aria-invalid={Boolean(nameError)} required autoFocus /></div>{nameError && <p className="auth-field-error">{nameError}</p>}</div>
    <div className="auth-field-group"><label htmlFor="register-email">Email</label><div className={emailError ? "auth-field invalid" : "auth-field"}><input id="register-email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" inputMode="email" type="email" aria-invalid={Boolean(emailError)} required /></div>{emailError && <p className="auth-field-error">{emailError}</p>}</div>
    <PasswordField id="register-password" label="Password" value={password} onChange={setPassword} autoComplete="new-password" error={passwordError} />
    <PasswordField id="register-confirmation" label="Confirm password" value={confirmation} onChange={setConfirmation} autoComplete="new-password" error={confirmationError} />
    <p className="auth-hint">Use 8 or more characters. Your password is never shown back to you.</p>
    {error && <p className="auth-error" role="alert">{error}</p>}
    <button className="auth-submit" type="submit" disabled={busy}><span>{busy ? "Creating account…" : "Create account"}</span><span aria-hidden="true">→</span></button>
    <p className="auth-switch">Already have an account? <Link to="/login">Sign in</Link></p>
  </form></AuthLayout>;
}
