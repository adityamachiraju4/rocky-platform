import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

const siteOrigin = "https://rockyos.in";
const siteName = "Rocky OS";
const companyName = "PhredSec Technologies Private Limited";
const ogImage = `${siteOrigin}/og-image.png`;

const routes = [
  {
    path: "/",
    file: "index.html",
    title: "Rocky OS | Personal Intelligence OS",
    description:
      "Rocky OS brings conversations, voice, projects, tasks, reminders, notes, lists and live context into one personal intelligence system.",
    eyebrow: "Rocky / Personal Intelligence OS",
    heading: "Everything you’re working on. Still connected.",
    lead:
      "Rocky brings your conversations, plans and everyday work into one personal system that remembers what matters.",
    sections: [
      ["Not another chatbot", "Rocky carries context forward and connects conversations to plans, reminders and work."],
      ["Continuity", "Talk. Remember. Plan. Act. Continue."],
      ["Capabilities", "Remember with context, notes and lists. Organize projects, tasks and reminders. Assist through voice, notifications and live information."],
      ["Voice", "Say it while you’re thinking it. Rocky supports natural voice interaction with clear listening, thinking and speaking states."],
      ["Trust by design", "Rocky emphasizes explicit permissions, visible system state, clear data boundaries and user control."],
    ],
  },
  {
    path: "/product",
    title: "Product | Rocky OS",
    description:
      "Explore how Rocky OS connects conversations, planning, memory and live information into a Personal Intelligence OS.",
    eyebrow: "Product",
    heading: "One system for personal context and forward motion.",
    lead: "Rocky is organized around continuity: what you said, what you planned, what changed and what needs attention next.",
    sections: [
      ["A connected personal workspace", "Projects, tasks, reminders, notes, lists and activity history live near the assistant."],
      ["Context when it matters", "Rocky can use relevant memory and live information tools to answer with more awareness of your current work."],
      ["Built for everyday loops", "Capture, remember, plan, check, update, remind and continue."],
    ],
  },
  {
    path: "/voice",
    title: "Voice | Rocky OS",
    description:
      "Rocky OS includes a voice-first experience for speaking, listening and continuing personal work with less friction.",
    eyebrow: "Voice",
    heading: "Speak naturally. Keep the thread alive.",
    lead: "Rocky’s voice experience is designed for quick capture, spoken replies and a smoother path between thought and action.",
    sections: [
      ["Voice-first, not voice-only", "Voice works alongside typing and structured pages, so you can speak when it is faster and review when precision matters."],
      ["Designed for noisy real life", "The Android voice path includes recording safeguards, silence handling and recovery from bad microphone capture sessions."],
      ["Conversational continuity", "Rocky is meant to carry context through the turn: what you asked, what it understood and what should happen next."],
    ],
  },
  {
    path: "/privacy",
    title: "Privacy | Rocky OS",
    description: "Read the conservative launch privacy overview for Rocky OS by PhredSec Technologies Private Limited.",
    eyebrow: "Privacy",
    heading: "Personal intelligence requires careful boundaries.",
    lead: "This overview explains the current privacy posture for Rocky OS before general availability.",
    sections: [
      ["Information Rocky may process", "Rocky may process account details, conversations, voice recordings submitted for transcription and user-created workspace data."],
      ["How information is used", "Information is used to provide the Rocky experience, maintain sessions, respond to requests, diagnose failures and protect the service."],
      ["User choices", "Users should be able to control what they enter, request deletion of account data and avoid optional permissions unless needed."],
    ],
  },
  {
    path: "/security",
    title: "Security | Rocky OS",
    description: "Rocky OS security overview covering account protection, transport security, permissions and responsible launch posture.",
    eyebrow: "Security",
    heading: "Security is part of the product surface.",
    lead: "Rocky is built around personal context, so the security posture must be practical, visible and continuously improved.",
    sections: [
      ["Account and transport protection", "Rocky uses token-based authentication and HTTPS for production network requests."],
      ["Native permissions", "Microphone and location permissions are requested only when needed for the corresponding user action."],
      ["Responsible disclosure", "Security contact details will be published before general availability."],
    ],
  },
  {
    path: "/about",
    title: "About | Rocky OS",
    description: "Rocky OS is developed by PhredSec Technologies Private Limited as a Personal Intelligence OS.",
    eyebrow: "About",
    heading: "Rocky is being built for continuity.",
    lead: "PhredSec Technologies Private Limited is developing Rocky OS as a personal intelligence layer for everyday planning, memory and initiative.",
    sections: [
      ["Why Rocky exists", "People spread their lives across chats, notes, reminders, lists and tools. Rocky exists to carry context forward."],
      ["Product philosophy", "Useful memory, visible controls, fast capture and a calm interface for repeated daily use."],
      ["Company", "Rocky OS is a product of PhredSec Technologies Private Limited."],
    ],
  },
  {
    path: "/download",
    title: "Download | Rocky OS",
    description: "Join early access for Rocky OS and learn about upcoming web and Android availability.",
    eyebrow: "Download",
    heading: "Rocky is preparing for early access.",
    lead: "The public app download is not open yet. Early access and store links will be published here when ready.",
    sections: [
      ["Current availability", "Rocky is being prepared for physical-device testing and public launch readiness."],
      ["Early access", "Early access details will be published on this page before general availability."],
      ["What to expect", "The first public experience will focus on conversations, voice, projects, tasks, reminders, notes, lists and activity continuity."],
    ],
  },
  {
    path: "/support",
    title: "Support | Rocky OS",
    description: "Support information for Rocky OS users, early testers and account questions.",
    eyebrow: "Support",
    heading: "Support will open with early access.",
    lead: "Support contact details will be published before general availability. This page will become the central support entry point.",
    sections: [
      ["Account help", "Account support procedures are being prepared alongside the public release."],
      ["Product feedback", "Early testers will receive the appropriate feedback path with their test invitation or release notes."],
      ["Service status", "If Rocky is unavailable during testing, check release notes and retry after the backend or app build has been updated."],
    ],
  },
  {
    path: "/terms",
    title: "Terms | Rocky OS",
    description: "Conservative draft terms overview for Rocky OS by PhredSec Technologies Private Limited.",
    eyebrow: "Terms",
    heading: "Terms for using Rocky OS.",
    lead: "These terms are a launch-oriented draft and should receive formal legal review before general availability.",
    sections: [
      ["Use of the service", "Rocky OS helps users manage personal context, conversations and productivity workflows."],
      ["Availability and changes", "Features may change during early access and the service may be interrupted for maintenance."],
      ["Limitations", "Users should verify important outputs before relying on them for critical decisions."],
    ],
  },
  {
    path: "/account-deletion",
    title: "Account Deletion | Rocky OS",
    description: "Learn how Rocky OS account deletion requests will be handled before general availability.",
    eyebrow: "Account deletion",
    heading: "Account deletion should be clear and human.",
    lead: "A self-service account deletion flow and confirmed contact path will be published before general availability.",
    sections: [
      ["Before public launch", "Early testers should use the account deletion instructions provided with their testing invitation or release notes."],
      ["What deletion should cover", "Account deletion is expected to cover account identity and user-created Rocky data where retention is not legally required."],
      ["Future self-service flow", "This page will be updated with exact steps once public account management is available."],
    ],
  },
];

const notFound = {
  path: "/404",
  file: "404.html",
  title: "Page Not Found | Rocky OS",
  description: "The requested Rocky OS page could not be found.",
  eyebrow: "404",
  heading: "This page drifted out of context.",
  lead: "The page you requested is not available. Return home to continue exploring Rocky OS.",
  sections: [["Back to Rocky", "Use the navigation or return to the homepage to find product, voice, privacy, security, support and early access information."]],
};

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function canonical(route) {
  return route.path === "/" ? siteOrigin : `${siteOrigin}${route.path}`;
}

function seoHead(route) {
  const url = canonical(route);
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": route.path === "/" ? "SoftwareApplication" : "WebPage",
    name: route.path === "/" ? siteName : route.title,
    description: route.description,
    url,
    applicationCategory: "ProductivityApplication",
    operatingSystem: "Web, Android",
    publisher: {
      "@type": "Organization",
      name: companyName,
    },
  };

  return `
    <title>${escapeHtml(route.title)}</title>
    <meta name="description" content="${escapeHtml(route.description)}" />
    <link rel="canonical" href="${url}" />
    <meta property="og:type" content="website" />
    <meta property="og:title" content="${escapeHtml(route.title)}" />
    <meta property="og:description" content="${escapeHtml(route.description)}" />
    <meta property="og:url" content="${url}" />
    <meta property="og:image" content="${ogImage}" />
    <meta property="og:site_name" content="${siteName}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="${escapeHtml(route.title)}" />
    <meta name="twitter:description" content="${escapeHtml(route.description)}" />
    <meta name="twitter:image" content="${ogImage}" />
    <script type="application/ld+json">${JSON.stringify(jsonLd)}</script>`;
}

function prerenderBody(route) {
  const routeLinks = routes
    .filter((item) => ["/product", "/voice", "/security", "/about"].includes(item.path))
    .map((item) => `<a href="${item.path}">${item.eyebrow}</a>`)
    .join("");
  const content = route.sections
    .map(([title, body]) => `<article class="content-block"><h2>${escapeHtml(title)}</h2><p>${escapeHtml(body)}</p></article>`)
    .join("");

  const brand = `<span class="brand-symbol" aria-hidden="true"><span></span></span><strong>Rocky OS</strong>`;
  const footer = `<footer class="footer"><div class="footer-brand"><a class="brand" href="/" aria-label="Rocky OS home">${brand}</a><p>Personal Intelligence OS</p><small>${companyName}</small></div><div class="footer-links"><nav aria-label="Product navigation"><span>Explore</span><a href="/product">Product</a><a href="/voice">Voice</a><a href="/security">Security</a></nav><nav aria-label="Company and legal navigation"><span>Company</span><a href="/about">About</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav><nav aria-label="Support navigation"><span>Support</span><a href="/support">Support</a><a href="/account-deletion">Account deletion</a><a href="/download">Early access</a></nav></div><p class="footer-note">© ${new Date().getFullYear()} Rocky OS</p></footer>`;

  if (route.path === "/") {
    return `<div class="site-shell"><a class="skip-link" href="#main">Skip to content</a><header class="site-header"><div class="header-inner"><a class="brand" href="/" aria-label="Rocky OS home">${brand}</a><nav class="nav" aria-label="Primary navigation">${routeLinks}</nav><a class="nav-cta" href="/download"><span class="desktop-label">Get early access</span><span class="mobile-label">Early access</span><span aria-hidden="true">↗</span></a></div></header><main id="main">
      <section class="product-hero"><div class="product-hero-copy"><p class="eyebrow">${escapeHtml(route.eyebrow)}</p><h1>${escapeHtml(route.heading)}</h1><p class="lead">${escapeHtml(route.lead)}</p><div class="hero-actions"><a class="button primary" href="/download">Get early access <span aria-hidden="true">↗</span></a><a class="text-action" href="#how-rocky-works">See how Rocky works <span aria-hidden="true">↓</span></a></div></div><div class="workspace-mockup hero-workspace"><div class="workspace-bar"><div class="workspace-brand">${brand}</div><div class="workspace-date">Wednesday, 3 September</div><div class="workspace-status"><i></i> All caught up</div></div><div class="workspace-layout"><aside class="workspace-nav"><span class="active">Conversation</span><span>Projects</span><span>Tasks</span><span>Notes</span><span>Lists</span><span>Activity</span></aside><div class="conversation-panel"><div class="conversation-heading"><span>Good afternoon.</span><h3>Three things need<br />your attention.</h3></div><div class="rocky-message">${brand}<div><p>Your website review is due today. The supplier call is at 3:30, and the Android release is waiting on verification.</p><span>Based on today’s work</span></div></div><div class="prompt-field"><span>Ask Rocky anything</span></div></div><aside class="today-panel"><div class="panel-title"><span>Today</span><small>3 open</small></div><div class="reminder-item"><span>Reminder · 3:30 PM</span><strong>Call supplier</strong></div><div class="note-item"><span>Note</span><p>Keep the launch page calm and product-led.</p></div></aside></div></div></section>
      <section class="story-section light-section connection-story" id="how-rocky-works"><div class="section-heading"><p class="eyebrow">From thought to follow-through</p><h2>Rocky keeps the<br />pieces connected.</h2></div><div class="connection-demo"><div class="spoken-request"><span>You say</span><p>“Remind me to follow up with the designer tomorrow.”</p></div><div class="connection-path"></div><div class="understood-objects"><span class="demo-label">Rocky understands</span><article class="product-object"><div><span>Project</span><strong>Website</strong></div></article><article class="product-object"><div><span>Task</span><strong>Review redesign</strong></div></article><article class="product-object"><div><span>Reminder</span><strong>Follow up tomorrow</strong></div></article></div></div></section>
      <section class="story-section dark-section workspace-story"><div class="section-heading"><p class="eyebrow">Your workspace</p><h2>One place to think,<br />plan and continue.</h2><p>Conversation and structured work live together.</p></div></section>
      <section class="story-section light-section voice-story"><div class="voice-story-copy"><p class="eyebrow">Rocky Voice</p><h2>Just say it.</h2><p>Capture a thought or ask what’s next without stopping to organize the interface first.</p></div></section>
      <section class="story-section dark-section continuity-story"><div class="section-heading"><p class="eyebrow">Continuity</p><h2>Rocky remembers<br />the thread.</h2><p>Return later and continue from the work itself, not a blank prompt.</p></div><div class="day-thread"><article><time>9:12 AM</time><div><span>You</span><p>Let’s work on the Android release.</p></div></article><article><time>Now</time><div><span>Rocky</span><p>We built the signed AAB and were waiting on Play Console verification.</p></div></article></div></section>
      <section class="story-section feature-section"><div class="feature-intro"><p class="eyebrow">What Rocky brings together</p><h2>Useful in the ways your day actually needs.</h2></div></section>
      <section class="story-section light-section trust-story"><div><p class="eyebrow">Trust by design</p><h2>Personal intelligence<br />needs boundaries.</h2></div><ul><li>You control permissions.</li><li>You see what Rocky is doing.</li><li>Sensitive actions stay explicit.</li><li>Context remains understandable.</li></ul></section>
      <section class="final-cta"><p class="eyebrow">Early access</p><h2>Ready when you are.</h2><p>Rocky is getting ready for its first public chapter.</p><a class="button primary" href="/download">Get early access <span aria-hidden="true">↗</span></a></section></main>${footer}</div>`;
  }

  return `<div class="site-shell">
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="site-header"><div class="header-inner"><a class="brand" href="/" aria-label="Rocky OS home">${brand}</a><nav class="nav" aria-label="Primary navigation">${routeLinks}</nav><a class="nav-cta" href="/download"><span class="desktop-label">Get early access</span><span class="mobile-label">Early access</span><span aria-hidden="true">↗</span></a></div></header>
    <main id="main">
      <section class="page-hero">
        <div class="hero-copy">
          <p class="eyebrow">${escapeHtml(route.eyebrow)}</p>
          <h1>${escapeHtml(route.heading)}</h1>
          <p class="lead">${escapeHtml(route.lead)}</p>
          ${route.path === "/404" ? '<a class="button primary" href="/">Return home <span aria-hidden="true">→</span></a>' : ''}
        </div>
      </section>
      <section class="section route-content">${content}</section>
    </main>
    ${footer}
  </div>`;
}

async function renderRoute(template, route) {
  const withHead = template
    .replace(/<title>.*?<\/title>/, "")
    .replace(/<meta\s+name="description"[^>]*>/, "")
    .replace("</head>", `${seoHead(route)}\n  </head>`);
  return withHead.replace('<div id="root"></div>', `<div id="root">${prerenderBody(route)}</div>`);
}

const template = await readFile("dist/index.html", "utf8");

for (const route of [...routes, notFound]) {
  const output = await renderRoute(template, route);
  const filePath = route.file
    ? join("dist", route.file)
    : join("dist", route.path.slice(1), "index.html");
  await mkdir(dirname(filePath), { recursive: true });
  await writeFile(filePath, output);
}
