import "./styles.css";
import { useLayoutEffect, type ReactNode } from "react";
import { COMPANY_NAME, CONTACT_EMAILS, PRODUCT_NAME, PRODUCT_TAGLINE, routeForPath, routes } from "./content";

function App() {
  const route = routeForPath(window.location.pathname);
  useSiteMotion();
  return <div className="site-shell">
    <a className="skip-link" href="#main">Skip to content</a>
    <Header />
    <main id="main">{route.path === "/" ? <HomePage /> : <RoutePage notFound={route.key === "not-found"} />}</main>
    <Footer />
  </div>;
}

function useSiteMotion() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const header = document.querySelector<HTMLElement>(".site-header");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    root.classList.add("motion-ready");
    const updateHeader = () => header?.classList.toggle("is-scrolled", window.scrollY > 12);
    updateHeader();
    window.addEventListener("scroll", updateHeader, { passive: true });
    if (reducedMotion || !("IntersectionObserver" in window)) {
      document.querySelectorAll<HTMLElement>("[data-reveal]").forEach((element) => element.classList.add("is-visible"));
      return () => window.removeEventListener("scroll", updateHeader);
    }
    const revealObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -7%", threshold: 0.08 });
    const voiceObserver = new IntersectionObserver(([entry]) => entry?.target.classList.toggle("is-motion-active", entry.isIntersecting), { threshold: 0.12 });
    document.querySelectorAll<HTMLElement>("[data-reveal]").forEach((element) => revealObserver.observe(element));
    const voiceSection = document.querySelector<HTMLElement>(".voice-story");
    if (voiceSection) voiceObserver.observe(voiceSection);
    return () => {
      window.removeEventListener("scroll", updateHeader);
      revealObserver.disconnect();
      voiceObserver.disconnect();
      root.classList.remove("motion-ready");
    };
  }, []);
}

function BrandMark() { return <span className="brand-symbol" aria-hidden="true"><span /></span>; }

function Header() {
  return <header className="site-header"><div className="header-inner">
    <a className="brand" href="/" aria-label="Rocky OS home"><BrandMark /><strong>{PRODUCT_NAME}</strong></a>
    <nav className="nav" aria-label="Primary navigation"><a href="/product">Product</a><a href="/voice">Voice</a><a href="/security">Security</a><a href="/about">About</a></nav>
    <a className="nav-cta" href="/download"><span className="desktop-label">Get early access</span><span className="mobile-label">Early access</span><span aria-hidden="true">↗</span></a>
  </div></header>;
}

function HomePage() {
  const home = routes[0];
  return <>
    <section className="product-hero">
      <div className="hero-brand-halo" aria-hidden="true"><img src="/rocky-presence.webp" alt="" /></div>
      <div className="product-hero-copy">
        <p className="eyebrow">{home.eyebrow}</p>
        <h1>{home.heading}</h1>
        <p className="lead">{home.lead}</p>
        <div className="hero-actions"><a className="button primary" href="/download">Get early access <span aria-hidden="true">↗</span></a><a className="text-action" href="#how-rocky-works">See how Rocky works <span aria-hidden="true">↓</span></a></div>
      </div>
      <WorkspaceMockup className="hero-workspace" />
    </section>

    <section className="story-section light-section connection-story" id="how-rocky-works" data-reveal>
      <SectionHeading eyebrow="From thought to follow-through" title={<>Rocky keeps the<br />pieces connected.</>} />
      <div className="connection-demo">
        <div className="spoken-request"><span>You say</span><p>“Remind me to follow up with the designer tomorrow.”</p><div className="mini-wave" aria-hidden="true"><i /><i /><i /><i /><i /></div></div>
        <div className="connection-path" aria-hidden="true"><i /><i /><i /></div>
        <div className="understood-objects">
          <span className="demo-label">Rocky understands</span>
          <ProductObject type="Project" title="Website" meta="Active project" />
          <ProductObject type="Task" title="Review redesign" meta="Open" />
          <ProductObject type="Reminder" title="Follow up with designer" meta="Tomorrow · 9:00 AM" />
        </div>
      </div>
    </section>

    <section className="story-section dark-section workspace-story" data-reveal>
      <SectionHeading eyebrow="Your workspace" title={<>One place to think,<br />plan and continue.</>} copy="Conversation and structured work live together, so good intentions have somewhere to go." />
      <WorkspaceMockup className="experience-workspace" expanded />
    </section>

    <section className="story-section light-section voice-story" data-reveal>
      <div className="voice-story-copy"><p className="eyebrow">Rocky Voice</p><h2>Just say it.</h2><p>Capture a thought, ask what’s next, or make a plan without stopping to organize the interface first.</p></div>
      <VoiceMockup />
    </section>

    <section className="story-section dark-section continuity-story" data-reveal>
      <SectionHeading eyebrow="Continuity" title={<>Rocky remembers<br />the thread.</>} copy="Return hours later—or days later—and continue from the work itself, not a blank prompt." />
      <div className="day-thread">
        <div className="thread-line" aria-hidden="true" />
        <article><time>9:12 AM</time><div><span>You</span><p>Let’s work on the Android release.</p></div></article>
        <article><time>3:46 PM</time><div><span>You</span><p>Where were we?</p></div></article>
        <article className="rocky-reply"><time>Now</time><div><span><BrandMark />Rocky</span><p>We built the signed AAB and were waiting on Play Console verification.</p><small>Rocky Android · Release</small></div></article>
      </div>
    </section>

    <section className="story-section feature-section" data-reveal>
      <div className="feature-intro"><p className="eyebrow">What Rocky brings together</p><h2>Useful in the ways your day actually needs.</h2></div>
      <div className="feature-row">
        <Feature index="01" name="Remember" body="Keep context close enough to become useful again."><div className="memory-visual"><i /><span>Project context</span><i /><span>Yesterday’s note</span></div></Feature>
        <Feature index="02" name="Organize" body="Turn open loops into projects, tasks and reminders."><div className="organize-visual"><span className="checked">✓</span><span /><span /></div></Feature>
        <Feature index="03" name="Act" body="Move from conversation to a clear next step."><div className="act-visual"><span>Thought</span><i>→</i><strong>Next step</strong></div></Feature>
        <Feature index="04" name="Inform" body="Bring live information into the context already at hand."><div className="inform-visual"><i /><span>Updated now</span></div></Feature>
      </div>
    </section>

    <section className="story-section light-section trust-story" data-reveal>
      <div><p className="eyebrow">Trust by design</p><h2>Personal intelligence<br />needs boundaries.</h2></div>
      <ul><li>You control permissions.</li><li>You see what Rocky is doing.</li><li>Sensitive actions stay explicit.</li><li>Context remains understandable.</li></ul>
    </section>

    <section className="final-cta" data-reveal>
      <div className="final-presence" aria-hidden="true"><img src="/rocky-presence.webp" alt="" /></div>
      <p className="eyebrow">Early access</p><h2>Ready when you are.</h2><p>Rocky is getting ready for its first public chapter.</p><a className="button primary" href="/download">Get early access <span aria-hidden="true">↗</span></a>
    </section>
  </>;
}

function SectionHeading({ eyebrow, title, copy }: { eyebrow: string; title: ReactNode; copy?: string }) {
  return <div className="section-heading"><p className="eyebrow">{eyebrow}</p><h2>{title}</h2>{copy && <p>{copy}</p>}</div>;
}

function ProductObject({ type, title, meta }: { type: string; title: string; meta: string }) {
  return <article className="product-object"><div className={`object-icon ${type.toLowerCase()}`} aria-hidden="true" /><div><span>{type}</span><strong>{title}</strong></div><small>{meta}</small></article>;
}

function WorkspaceMockup({ className, expanded = false }: { className: string; expanded?: boolean }) {
  return <div className={`workspace-mockup ${className} ${expanded ? "is-expanded" : ""}`} aria-label="Rocky personal workspace preview">
    <div className="workspace-bar"><div className="workspace-brand"><BrandMark /><strong>Rocky</strong></div><div className="workspace-date">Wednesday, 3 September</div><div className="workspace-status"><i /> All caught up</div></div>
    <div className="workspace-layout">
      <aside className="workspace-nav"><span className="active">Conversation</span><span>Projects</span><span>Tasks <b>3</b></span><span>Notes</span><span>Lists</span><span>Activity</span><div className="nav-project"><small>Active project</small><strong>Rocky Android</strong><i><span /></i></div></aside>
      <div className="conversation-panel">
        <div className="conversation-heading"><span>Good afternoon.</span><h3>Three things need<br />your attention.</h3></div>
        <div className="rocky-message"><BrandMark /><div><p>Your website review is due today. The supplier call is at 3:30, and the Android release is waiting on verification.</p><span>Based on today’s work</span></div></div>
        <div className="prompt-field"><span>Ask Rocky anything</span><div className="prompt-actions"><i className="voice-dot" /><b>↑</b></div></div>
      </div>
      <aside className="today-panel"><div className="panel-title"><span>Today</span><small>3 open</small></div><label><input type="checkbox" disabled /><i />Finish website redesign<small>Website</small></label><label><input type="checkbox" disabled /><i />Follow up on D-U-N-S<small>Company</small></label><div className="reminder-item"><span>Reminder · 3:30 PM</span><strong>Call supplier</strong></div><div className="note-item"><span>Note</span><p>Keep the launch page calm and product-led.</p></div></aside>
    </div>
  </div>;
}

function VoiceMockup() {
  return <div className="voice-mockup" aria-label="Rocky listening interface">
    <div className="voice-top"><BrandMark /><span>Rocky Voice</span><small>Listening</small></div>
    <div className="voice-center"><div className="voice-ring"><i /><i /><i /></div><p>“What’s left today?”</p><div className="voice-waveform" aria-hidden="true"><i /><i /><i /><i /><i /><i /><i /></div></div>
    <div className="voice-examples"><span>Remind me tomorrow.</span><span>Add this to the Rocky project.</span></div>
    <div className="voice-progress"><span className="done">Listening</span><i /><span>Thinking</span><i /><span>Done</span></div>
  </div>;
}

function Feature({ index, name, body, children }: { index: string; name: string; body: string; children: ReactNode }) {
  return <article><span className="feature-number">{index}</span><h3>{name}</h3><p>{body}</p>{children}</article>;
}

function RoutePage({ notFound }: { notFound: boolean }) {
  const route = routeForPath(window.location.pathname);
  return <><section className={`page-hero ${notFound ? "not-found" : ""}`} data-reveal><p className="eyebrow">{route.eyebrow}</p><h1>{route.heading}</h1><p className="lead">{route.lead}</p>{notFound && <a className="button primary" href="/">Return home <span aria-hidden="true">→</span></a>}</section>
    <section className="route-content">{route.sections.map((section, index) => <article className="content-block" data-reveal key={section.title}><span className="content-index">0{index + 1}</span><div><h2>{section.title}</h2><p>{section.body}</p>{section.items && <ul>{section.items.map((item) => <li key={item}>{item}</li>)}</ul>}{section.contacts?.map((contact) => <p className="contact-link" key={contact.email}>{contact.label}: <a href={`mailto:${contact.email}`}>{contact.email}</a></p>)}</div></article>)}</section></>;
}

function Footer() {
  return <footer className="footer"><div className="footer-brand"><a className="brand" href="/" aria-label="Rocky OS home"><BrandMark /><strong>{PRODUCT_NAME}</strong></a><p>{PRODUCT_TAGLINE}</p><small>{COMPANY_NAME}</small></div>
    <div className="footer-links"><nav aria-label="Product navigation"><span>Explore</span><a href="/product">Product</a><a href="/voice">Voice</a><a href="/security">Security</a></nav><nav aria-label="Company and legal navigation"><span>Company</span><a href="/about">About</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav><nav aria-label="Support navigation"><span>Support</span><a href="/support">Support</a><a href={`mailto:${CONTACT_EMAILS.support}`}>{CONTACT_EMAILS.support}</a><a href="/account-deletion">Account deletion</a><a href="/download">Early access</a></nav></div><p className="footer-note">© {new Date().getFullYear()} Rocky OS</p>
  </footer>;
}

export default App;
