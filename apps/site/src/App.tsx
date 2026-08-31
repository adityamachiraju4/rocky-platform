import "./styles.css";
import {
  COMPANY_NAME,
  PRODUCT_NAME,
  PRODUCT_TAGLINE,
  navRoutes,
  routeForPath,
  routes,
} from "./content";

const capabilityCards = [
  ["Conversations", "Natural requests grounded in the work you keep in Rocky."],
  ["Voice", "Fast capture and spoken replies for moments when typing slows you down."],
  ["Projects", "Long-running outcomes with the tasks and context that support them."],
  ["Reminders", "Time-bound follow-through without scattering obligations."],
  ["Notes and lists", "Simple structured memory for things worth keeping close."],
  ["Activity", "A visible record of what changed and what Rocky helped with."],
];

function App() {
  const route = routeForPath(window.location.pathname);
  const isHome = route.path === "/";
  const isNotFound = route.key === "not-found";

  return (
    <div className="site-shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <Header />
      <main id="main">
        {isHome ? <HomePage /> : <RoutePage notFound={isNotFound} />}
      </main>
      <Footer />
    </div>
  );
}

function Header() {
  return (
    <header className="site-header">
      <a className="brand" href="/" aria-label="Rocky OS home">
        <span className="brand-mark" aria-hidden="true">R</span>
        <span>
          <strong>{PRODUCT_NAME}</strong>
          <small>{PRODUCT_TAGLINE}</small>
        </span>
      </a>
      <nav className="nav" aria-label="Primary navigation">
        {navRoutes.map((route) => (
          <a key={route.path} href={route.path}>
            {route.path === "/" ? "Home" : route.eyebrow}
          </a>
        ))}
      </nav>
      <a className="nav-cta" href="/download">Early access</a>
    </header>
  );
}

function HomePage() {
  const home = routes[0];
  return (
    <>
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">{home.eyebrow}</p>
          <h1>{home.heading}</h1>
          <p className="lead">{home.lead}</p>
          <div className="hero-actions" aria-label="Rocky OS primary actions">
            <a className="button primary" href="/download">Join early access</a>
            <a className="button secondary" href="/product">Explore product</a>
          </div>
        </div>
        <RockyVisual />
      </section>

      <section className="section intro-section">
        <div>
          <p className="eyebrow">Personal Intelligence OS</p>
          <h2>Built for the continuity missing from everyday software.</h2>
        </div>
        <p>
          Rocky brings projects, tasks, reminders, notes, conversations and live information into
          one continuously useful system. It is not a blank chat box asking you to start over. It is
          a place for context to accumulate, stay legible and become action.
        </p>
      </section>

      <section className="section comparison">
        <div className="section-heading">
          <p className="eyebrow">Different by design</p>
          <h2>Rocky is closer to an operating layer for your life than a chatbot tab.</h2>
        </div>
        <div className="compare-grid">
          <article>
            <span>Normal chatbot</span>
            <h3>Starts from the prompt.</h3>
            <p>Useful for one-off answers, but often disconnected from your plans, memory and ongoing work.</p>
          </article>
          <article>
            <span>Rocky OS</span>
            <h3>Starts from continuity.</h3>
            <p>Designed around remembered context, visible work objects, voice, reminders and follow-through.</p>
          </article>
        </div>
      </section>

      <section className="section">
        <div className="section-heading">
          <p className="eyebrow">Capabilities</p>
          <h2>One calm surface for the parts of life that keep moving.</h2>
        </div>
        <div className="card-grid">
          {capabilityCards.map(([title, body]) => (
            <article className="capability-card" key={title}>
              <h3>{title}</h3>
              <p>{body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section split-band">
        <div>
          <p className="eyebrow">Voice first</p>
          <h2>Capture the thought before it evaporates.</h2>
        </div>
        <p>
          Rocky’s voice path is designed for spoken requests, transcription, response generation and
          spoken playback while preserving manual stop, silence handling and clear state.
        </p>
      </section>

      <section className="section trust-band">
        <div>
          <p className="eyebrow">Trust and control</p>
          <h2>Personal intelligence should stay understandable.</h2>
        </div>
        <div className="trust-list">
          <p>Explicit permissions for sensitive device capabilities.</p>
          <p>Token-based authentication, with provider secrets kept server-side.</p>
          <p>Visible status for listening, thinking, speaking and recovery paths.</p>
        </div>
      </section>

      <section className="cta-section">
        <p className="eyebrow">Early access</p>
        <h2>Rocky OS is preparing for public availability.</h2>
        <p>Download and early access details will be published as testing opens.</p>
        <a className="button primary" href="/download">View download status</a>
      </section>
    </>
  );
}

function RockyVisual() {
  return (
    <div className="presence-panel" aria-label="Rocky visual presence">
      <picture>
        <source srcSet="/rocky-presence.webp" type="image/webp" />
        <source srcSet="/rocky-presence-1200.png" type="image/png" />
        <img
          src="/rocky-presence-1200.png"
          alt="Rocky OS abstract luminous presence"
          width="1200"
          height="630"
        />
      </picture>
      <div className="signal-card">
        <span>Continuity</span>
        <strong>Context, planning and voice in one system.</strong>
      </div>
    </div>
  );
}

function RoutePage({ notFound }: { notFound: boolean }) {
  const route = routeForPath(window.location.pathname);

  return (
    <>
      <section className={`page-hero ${notFound ? "not-found" : ""}`}>
        <p className="eyebrow">{route.eyebrow}</p>
        <h1>{route.heading}</h1>
        <p className="lead">{route.lead}</p>
        {notFound && <a className="button primary" href="/">Return home</a>}
      </section>
      <section className="section route-content">
        {route.sections.map((section) => (
          <article className="content-block" key={section.title}>
            <h2>{section.title}</h2>
            <p>{section.body}</p>
            {section.items && (
              <ul>
                {section.items.map((item) => <li key={item}>{item}</li>)}
              </ul>
            )}
          </article>
        ))}
      </section>
    </>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <div>
        <strong>{PRODUCT_NAME}</strong>
        <p>{PRODUCT_TAGLINE} by {COMPANY_NAME}.</p>
      </div>
      <nav aria-label="Footer navigation">
        <a href="/privacy">Privacy</a>
        <a href="/security">Security</a>
        <a href="/terms">Terms</a>
        <a href="/support">Support</a>
        <a href="/account-deletion">Account deletion</a>
      </nav>
    </footer>
  );
}

export default App;
