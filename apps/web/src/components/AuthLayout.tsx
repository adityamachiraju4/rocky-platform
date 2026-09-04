import type { ReactNode } from "react";
import BrandMark from "./BrandMark";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell">
      <a className="skip-link" href="#auth-main">Skip to form</a>
      <aside className="auth-story" aria-label="About Rocky OS">
        <div className="auth-brand"><BrandMark /><span>Rocky OS</span></div>
        <div className="auth-story-copy">
          <p className="auth-eyebrow">Personal Intelligence OS</p>
          <h2>Everything you’re working on. Still connected.</h2>
          <p>One calm place for conversations, projects, tasks, reminders and the context that carries your day forward.</p>
        </div>
        <div className="auth-presence" aria-hidden="true"><i /><i /><i /></div>
        <p className="auth-footnote">Your workspace, ready when you are.</p>
      </aside>
      <main className="auth-main" id="auth-main">{children}</main>
    </div>
  );
}
