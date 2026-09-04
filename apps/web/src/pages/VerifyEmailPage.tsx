import { useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { confirmEmailVerification, requestEmailVerification } from "../api";
import AuthLayout from "../components/AuthLayout";

interface VerifyLocationState { email?: string; sent?: boolean }
type VerifyState = "checking" | "verified" | "invalid" | "waiting";

export default function VerifyEmailPage() {
  const location = useLocation();
  const routeState = location.state as VerifyLocationState | null;
  const [params] = useSearchParams();
  const token = params.get("token");
  const [email, setEmail] = useState(routeState?.email ?? "");
  const [state, setState] = useState<VerifyState>(token ? "checking" : "waiting");
  const [resending, setResending] = useState(false);
  const [message, setMessage] = useState<string | null>(
    !token && routeState?.sent === false ? "Your account was created, but email delivery failed. You can retry below." : null,
  );

  useEffect(() => {
    if (!token) return;
    let active = true;
    void confirmEmailVerification(token)
      .then(() => { if (active) setState("verified"); })
      .catch(() => { if (active) setState("invalid"); });
    return () => { active = false; };
  }, [token]);

  const resend = async () => {
    if (!email.trim() || resending) return;
    setResending(true); setMessage(null);
    try { await requestEmailVerification(email.trim()); setMessage("If the account is eligible, a verification link has been sent."); }
    catch { setMessage("Rocky couldn’t submit that request. Please try again shortly."); }
    finally { setResending(false); }
  };

  return <AuthLayout><section className="auth-form auth-static" aria-labelledby="verification-title">
    <div className="auth-heading"><p className="auth-eyebrow">Email verification</p><h1 id="verification-title">{state === "checking" ? "Verifying your email…" : state === "verified" ? "Email verified." : state === "invalid" ? "That link has expired." : "Check your email."}</h1><p>{state === "verified" ? "Your Rocky workspace is ready for you." : state === "invalid" ? "Verification links are single-use and expire after 24 hours. Request another below." : token ? "This should only take a moment." : "Open the verification link Rocky sent to finish setting up your account."}</p></div>
    {state === "verified" ? <Link className="auth-submit link" to="/login"><span>Continue to sign in</span><span aria-hidden="true">→</span></Link> : state !== "checking" && <>
      {!routeState?.email && <div className="auth-field-group"><label htmlFor="verification-email">Email</label><div className="auth-field"><input id="verification-email" type="email" inputMode="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></div></div>}
      <button className="auth-submit" type="button" disabled={!email.trim() || resending} onClick={resend}><span>{resending ? "Sending…" : "Resend verification email"}</span><span aria-hidden="true">→</span></button>
      {message && <p className="auth-response" role="status">{message}</p>}
      <p className="auth-switch"><Link to="/login">Return to sign in</Link></p>
    </>}
  </section></AuthLayout>;
}
