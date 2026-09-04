import { useCallback, useEffect, useRef, useState } from "react";
import BrandMark from "./BrandMark";

const STORAGE_KEY = "rocky.onboarding.v1";
const steps = [
  { eyebrow: "Welcome", title: "Welcome to Rocky.", copy: "A personal intelligence workspace that helps the pieces of your day stay connected.", detail: "Start with a thought. Rocky can help turn it into work you can continue." },
  { eyebrow: "Your workspace", title: "Think, plan and follow through.", copy: "Talk with Rocky, then keep projects, tasks, reminders, notes and lists close to the conversation.", detail: "You stay in control of what gets created or changed." },
  { eyebrow: "Voice", title: "Speak when typing is friction.", copy: "The microphone is only requested when you choose the mic button. You can always keep typing.", detail: "Rocky will explain any permission request in context." },
  { eyebrow: "Notifications", title: "Only when it’s useful.", copy: "Notifications are optional and will be requested when you enable a feature that needs them—not during setup.", detail: "You can change notification choices later in Settings." },
  { eyebrow: "Ready", title: "What should we work on?", copy: "Your workspace is ready. Ask Rocky a question, capture something, or continue with the work already in motion.", detail: "You can revisit permissions and preferences in Settings." },
] as const;

function isComplete(): boolean {
  try { return window.localStorage.getItem(STORAGE_KEY) === "complete"; }
  catch { return false; }
}

export default function Onboarding() {
  const [step, setStep] = useState(0);
  const [open, setOpen] = useState(() => !isComplete());
  const primaryActionRef = useRef<HTMLButtonElement | null>(null);
  const finish = useCallback(() => {
    try { window.localStorage.setItem(STORAGE_KEY, "complete"); } catch { /* best-effort preference */ }
    setOpen(false);
  }, []);
  useEffect(() => {
    if (!open) return;
    primaryActionRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") finish(); };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [finish, open]);
  if (!open) return null;
  const current = steps[step];
  return <div className="onboarding-backdrop" role="presentation">
    <section className="onboarding" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
      <div className="onboarding-top"><div className="onboarding-brand"><BrandMark compact /><span>Rocky OS</span></div><button type="button" onClick={finish}>Skip</button></div>
      <div className="onboarding-progress" aria-label={`Step ${step + 1} of ${steps.length}`}>{steps.map((_, index) => <i className={index <= step ? "active" : ""} key={index} />)}</div>
      <div className="onboarding-copy" key={step}><p className="auth-eyebrow">{current.eyebrow}</p><h2 id="onboarding-title">{current.title}</h2><p>{current.copy}</p><small>{current.detail}</small></div>
      <div className="onboarding-actions">
        {step > 0 && <button className="onboarding-back" type="button" onClick={() => setStep((value) => value - 1)}>Back</button>}
        <button ref={primaryActionRef} className="auth-submit" type="button" onClick={() => step === steps.length - 1 ? finish() : setStep((value) => value + 1)}><span>{step === steps.length - 1 ? "Enter Rocky" : "Continue"}</span><span aria-hidden="true">→</span></button>
      </div>
    </section>
  </div>;
}
