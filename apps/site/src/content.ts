export const SITE_ORIGIN = "https://rockyos.in";
export const COMPANY_NAME = "PhredSec Technologies Private Limited";
export const PRODUCT_NAME = "Rocky OS";
export const PRODUCT_TAGLINE = "Personal Intelligence OS";
export const CONTACT_EMAILS = {
  support: "support@rockyos.in",
  hello: "hello@rockyos.in",
  security: "security@rockyos.in",
  privacy: "privacy@rockyos.in",
  billing: "billing@rockyos.in",
  contact: "contact@rockyos.in",
} as const;

export type RouteKey =
  | "home"
  | "product"
  | "voice"
  | "privacy"
  | "security"
  | "about"
  | "download"
  | "support"
  | "terms"
  | "account-deletion"
  | "not-found";

export interface SiteRoute {
  key: RouteKey;
  path: string;
  title: string;
  description: string;
  eyebrow: string;
  heading: string;
  lead: string;
  sections: Array<{
    title: string;
    body: string;
    items?: string[];
    contacts?: Array<{ label: string; email: string }>;
  }>;
}

export const routes: SiteRoute[] = [
  {
    key: "home",
    path: "/",
    title: "Rocky OS | Personal Intelligence OS",
    description:
      "Rocky OS brings conversations, voice, projects, tasks, reminders, notes, lists and live context into one personal intelligence system.",
    eyebrow: "Rocky / Personal Intelligence OS",
    heading: "Everything you’re working on. Still connected.",
    lead:
      "Rocky brings your conversations, plans and everyday work into one personal system that remembers what matters.",
    sections: [
      {
        title: "More than another chatbot",
        body:
          "Rocky is being built around the shape of your actual day: ongoing projects, open loops, reminders, notes, lists, history and the live information needed to act.",
        items: ["Continuity over isolated prompts", "A workspace for plans and memory", "Voice-first interaction when typing is friction"],
      },
      {
        title: "Core capability areas",
        body:
          "The current Rocky system includes conversations, voice, projects, tasks, reminders, notes, lists, notifications, activity history, memory context and live information tools.",
      },
      {
        title: "Control stays visible",
        body:
          "Rocky should help without hiding the mechanics. The product direction emphasizes explicit user control, clear status, recoverable actions and careful handling of personal context.",
      },
    ],
  },
  {
    key: "product",
    path: "/product",
    title: "Product | Rocky OS",
    description:
      "Explore how Rocky OS connects conversations, planning, memory and live information into a Personal Intelligence OS.",
    eyebrow: "Product",
    heading: "One system for personal context and forward motion.",
    lead:
      "Rocky is organized around continuity: what you said, what you planned, what changed and what needs attention next.",
    sections: [
      {
        title: "A connected personal workspace",
        body:
          "Projects, tasks, reminders, notes, lists and activity history live near the assistant, so conversations can become structured follow-through instead of disappearing into chat history.",
      },
      {
        title: "Context when it matters",
        body:
          "Rocky can use relevant memory and live information tools to answer with more awareness of your current work, while preserving explicit user interaction for sensitive actions.",
      },
      {
        title: "Built for everyday loops",
        body:
          "The product is designed for recurring patterns: capture, remember, plan, check, update, remind and continue.",
      },
    ],
  },
  {
    key: "voice",
    path: "/voice",
    title: "Voice | Rocky OS",
    description:
      "Rocky OS includes a voice-first experience for speaking, listening and continuing personal work with less friction.",
    eyebrow: "Voice",
    heading: "Speak naturally. Keep the thread alive.",
    lead:
      "Rocky’s voice experience is designed for quick capture, spoken replies and a smoother path between thought and action.",
    sections: [
      {
        title: "Voice-first, not voice-only",
        body:
          "Voice works alongside typing and structured pages, so you can speak when it is faster and review details when precision matters.",
      },
      {
        title: "Designed for noisy real life",
        body:
          "The Android voice path includes recording safeguards, silence handling and recovery from bad microphone capture sessions while keeping user-initiated Stop predictable.",
      },
      {
        title: "Conversational continuity",
        body:
          "The goal is not just transcription. Rocky is meant to carry context through the turn: what you asked, what it understood and what should happen next.",
      },
    ],
  },
  {
    key: "privacy",
    path: "/privacy",
    title: "Privacy | Rocky OS",
    description:
      "Read the conservative launch privacy overview for Rocky OS by PhredSec Technologies Private Limited.",
    eyebrow: "Privacy",
    heading: "Personal intelligence requires careful boundaries.",
    lead:
      "This overview explains the current privacy posture for Rocky OS before general availability. A formal policy should be reviewed before public launch.",
    sections: [
      {
        title: "Information Rocky may process",
        body:
          "Rocky may process account details, conversations, voice recordings submitted for transcription, projects, tasks, reminders, notes, lists, notifications, activity history and user-provided context needed to operate the service.",
      },
      {
        title: "How information is used",
        body:
          "Information is used to provide the Rocky experience, maintain sessions, respond to requests, improve reliability, diagnose failures and protect the service from abuse.",
      },
      {
        title: "User choices",
        body:
          "Users should be able to control what they enter, request deletion of account data and avoid optional permissions unless a feature requires them.",
        contacts: [{ label: "Privacy and data requests", email: CONTACT_EMAILS.privacy }],
      },
    ],
  },
  {
    key: "security",
    path: "/security",
    title: "Security | Rocky OS",
    description:
      "Rocky OS security overview covering account protection, transport security, permissions and responsible launch posture.",
    eyebrow: "Security",
    heading: "Security is part of the product surface.",
    lead:
      "Rocky is built around personal context, so the security posture must be practical, visible and continuously improved.",
    sections: [
      {
        title: "Account and transport protection",
        body:
          "Rocky uses token-based authentication and HTTPS for production network requests. Secrets and provider keys belong on the backend, not in public web or native bundles.",
      },
      {
        title: "Native permissions",
        body:
          "Microphone and location permissions are requested only when needed for the corresponding user action. Android debug certificate trust is kept out of release builds.",
      },
      {
        title: "Responsible disclosure",
        body:
          "Report suspected vulnerabilities or security incidents directly to the Rocky security team.",
        contacts: [{ label: "Security reports", email: CONTACT_EMAILS.security }],
      },
    ],
  },
  {
    key: "about",
    path: "/about",
    title: "About | Rocky OS",
    description:
      "Rocky OS is developed by PhredSec Technologies Private Limited as a Personal Intelligence OS.",
    eyebrow: "About",
    heading: "Rocky is being built for continuity.",
    lead:
      "PhredSec Technologies Private Limited is developing Rocky OS as a personal intelligence layer for everyday planning, memory and initiative.",
    sections: [
      {
        title: "Why Rocky exists",
        body:
          "People already spread their lives across chats, notes, reminders, lists and tools. Rocky’s purpose is to make that context easier to carry forward.",
      },
      {
        title: "Product philosophy",
        body:
          "The system should feel capable without being opaque: useful memory, visible controls, fast capture and a calm interface for repeated daily use.",
      },
      {
        title: "Company",
        body:
          "Rocky OS is a product of PhredSec Technologies Private Limited.",
        contacts: [{ label: "General enquiries", email: CONTACT_EMAILS.hello }],
      },
    ],
  },
  {
    key: "download",
    path: "/download",
    title: "Download | Rocky OS",
    description:
      "Join early access for Rocky OS and learn about upcoming web and Android availability.",
    eyebrow: "Download",
    heading: "Rocky is preparing for early access.",
    lead:
      "The public app download is not open yet. Early access and store links will be published here when they are ready.",
    sections: [
      {
        title: "Current availability",
        body:
          "Rocky is being prepared for physical-device testing and public launch readiness. Do not install builds from unofficial sources.",
      },
      {
        title: "Early access",
        body:
          "Early access details will be published on this page before general availability, including supported platforms and testing instructions.",
        contacts: [{ label: "Early-access enquiries", email: CONTACT_EMAILS.hello }],
      },
      {
        title: "What to expect",
        body:
          "The first public experience will focus on conversations, voice, projects, tasks, reminders, notes, lists and activity continuity.",
      },
    ],
  },
  {
    key: "support",
    path: "/support",
    title: "Support | Rocky OS",
    description:
      "Support information for Rocky OS users, early testers and account questions.",
    eyebrow: "Support",
    heading: "Help when you need it.",
    lead:
      "This is the central support entry point for Rocky accounts, product questions and troubleshooting.",
    sections: [
      {
        title: "Account help",
        body:
          "Contact Rocky support for account access, verification and recovery help. Never share passwords or private tokens with anyone claiming to provide support.",
        contacts: [{ label: "Customer support", email: CONTACT_EMAILS.support }],
      },
      {
        title: "Product feedback",
        body:
          "Questions and feedback from early testers are welcome through the support address.",
        contacts: [{ label: "Product help and feedback", email: CONTACT_EMAILS.support }],
      },
      {
        title: "Service status",
        body:
          "If Rocky is unavailable during testing, check the release notes and retry after the backend or app build has been updated.",
      },
    ],
  },
  {
    key: "terms",
    path: "/terms",
    title: "Terms | Rocky OS",
    description:
      "Conservative draft terms overview for Rocky OS by PhredSec Technologies Private Limited.",
    eyebrow: "Terms",
    heading: "Terms for using Rocky OS.",
    lead:
      "These terms are a launch-oriented draft and should receive formal legal review before general availability.",
    sections: [
      {
        title: "Use of the service",
        body:
          "Rocky OS is provided to help users manage personal context, conversations and productivity workflows. Users are responsible for the information they submit and the decisions they make using the service.",
      },
      {
        title: "Availability and changes",
        body:
          "Features may change during early access. The service may be interrupted for maintenance, reliability improvements or provider availability.",
      },
      {
        title: "Limitations",
        body:
          "Rocky may produce incomplete or incorrect information. Users should verify important outputs before relying on them for legal, medical, financial or safety-critical decisions.",
      },
      {
        title: "Questions",
        body: "Contact Rocky support with questions about these terms or use of the service.",
        contacts: [{ label: "Terms support", email: CONTACT_EMAILS.support }],
      },
    ],
  },
  {
    key: "account-deletion",
    path: "/account-deletion",
    title: "Account Deletion | Rocky OS",
    description:
      "Learn how Rocky OS account deletion requests will be handled before general availability.",
    eyebrow: "Account deletion",
    heading: "Account deletion should be clear and human.",
    lead:
      "Until self-service deletion is available, you can request deletion directly from the Rocky privacy team.",
    sections: [
      {
        title: "Before public launch",
        body:
          "Early testers can request account deletion by email. Support can help if you cannot access your account.",
        contacts: [
          { label: "Account deletion and data requests", email: CONTACT_EMAILS.privacy },
          { label: "Account support", email: CONTACT_EMAILS.support },
        ],
      },
      {
        title: "What deletion should cover",
        body:
          "Account deletion is expected to cover account identity and user-created Rocky data such as conversations, projects, tasks, reminders, notes, lists and related activity where retention is not legally required.",
      },
      {
        title: "Future self-service flow",
        body:
          "This page will be updated with exact steps once the public account management flow is available.",
      },
    ],
  },
];

export const notFoundRoute: SiteRoute = {
  key: "not-found",
  path: "/404",
  title: "Page Not Found | Rocky OS",
  description: "The requested Rocky OS page could not be found.",
  eyebrow: "404",
  heading: "This page drifted out of context.",
  lead: "The page you requested is not available. Return home to continue exploring Rocky OS.",
  sections: [
    {
      title: "Back to Rocky",
      body: "Use the navigation or return to the homepage to find product, voice, privacy, security, support and early access information.",
    },
  ],
};

export const navRoutes = routes.filter((route) =>
  ["/", "/product", "/voice", "/privacy", "/security", "/about", "/download"].includes(route.path),
);

export function routeForPath(pathname: string): SiteRoute {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  return routes.find((route) => route.path === normalized) ?? notFoundRoute;
}

export function canonicalUrl(path: string): string {
  return path === "/" ? SITE_ORIGIN : `${SITE_ORIGIN}${path}`;
}
