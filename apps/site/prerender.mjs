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
    eyebrow: "Rocky OS",
    heading: "Your life deserves its own intelligence.",
    lead:
      "Rocky is a Personal Intelligence OS designed to keep your context, plans and everyday work moving with continuity and control.",
    sections: [
      ["Personal Intelligence OS", "Built for the continuity missing from everyday software."],
      ["Different by design", "Rocky is closer to an operating layer for your life than a chatbot tab."],
      ["Capabilities", "Conversations, voice, projects, tasks, reminders, notes, lists, notifications, activity history, memory context and live information tools."],
      ["Trust and control", "Rocky emphasizes explicit user control, clear status and careful handling of personal context."],
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
    .filter((item) => ["/", "/product", "/voice", "/privacy", "/security", "/about", "/download"].includes(item.path))
    .map((item) => `<a href="${item.path}">${item.path === "/" ? "Home" : item.eyebrow}</a>`)
    .join("");
  const content = route.sections
    .map(([title, body]) => `<article class="content-block"><h2>${escapeHtml(title)}</h2><p>${escapeHtml(body)}</p></article>`)
    .join("");

  const heroMedia = route.path === "/"
    ? `<div class="presence-panel" aria-label="Rocky visual presence"><picture><source srcset="/rocky-presence.webp" type="image/webp" /><source srcset="/rocky-presence-1200.png" type="image/png" /><img src="/rocky-presence-1200.png" alt="Rocky OS abstract luminous presence" width="1200" height="630" /></picture><div class="signal-card"><span>Continuity</span><strong>Context, planning and voice in one system.</strong></div></div>`
    : "";

  return `<div class="site-shell">
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="site-header">
      <a class="brand" href="/" aria-label="Rocky OS home"><span class="brand-mark" aria-hidden="true">R</span><span><strong>Rocky OS</strong><small>Personal Intelligence OS</small></span></a>
      <nav class="nav" aria-label="Primary navigation">${routeLinks}</nav>
      <a class="nav-cta" href="/download">Early access</a>
    </header>
    <main id="main">
      <section class="${route.path === "/" ? "hero" : "page-hero"}">
        <div class="hero-copy">
          <p class="eyebrow">${escapeHtml(route.eyebrow)}</p>
          <h1>${escapeHtml(route.heading)}</h1>
          <p class="lead">${escapeHtml(route.lead)}</p>
          ${route.path === "/404" ? '<a class="button primary" href="/">Return home</a>' : '<div class="hero-actions"><a class="button primary" href="/download">Join early access</a><a class="button secondary" href="/product">Explore product</a></div>'}
        </div>
        ${heroMedia}
      </section>
      <section class="section route-content">${content}</section>
    </main>
    <footer class="footer"><div><strong>Rocky OS</strong><p>Personal Intelligence OS by ${companyName}.</p></div><nav aria-label="Footer navigation"><a href="/privacy">Privacy</a><a href="/security">Security</a><a href="/terms">Terms</a><a href="/support">Support</a><a href="/account-deletion">Account deletion</a></nav></footer>
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
