import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./useAuth";
import BrandMark from "./components/BrandMark";
import Onboarding from "./components/Onboarding";

type IconName = "home" | "projects" | "tasks" | "reminders" | "notes" | "lists" | "activity" | "settings" | "more" | "notifications" | "signout";

const navigation = [
  { label: "Mission Control", shortLabel: "Home", icon: "home", to: "/", end: true },
  { label: "Projects", shortLabel: "Projects", icon: "projects", to: "/projects" },
  { label: "Tasks", shortLabel: "Tasks", icon: "tasks", to: "/tasks" },
  { label: "Reminders", shortLabel: "Reminders", icon: "reminders", to: "/reminders" },
  { label: "Notes", shortLabel: "Notes", icon: "notes", to: "/notes" },
  { label: "Lists", shortLabel: "Lists", icon: "lists", to: "/lists" },
  { label: "Activity", shortLabel: "Activity", icon: "activity", to: "/activity" },
  { label: "Settings", shortLabel: "Settings", icon: "settings", to: "/settings" },
] as const satisfies ReadonlyArray<{ label: string; shortLabel: string; icon: IconName; to: string; end?: boolean }>;

const mobilePrimary = ["/", "/projects", "/tasks", "/activity"];
const mobileMore = [
  ...navigation.filter((item) => ["/reminders", "/notes", "/lists"].includes(item.to)),
  { label: "Notifications", shortLabel: "Notifications", icon: "notifications" as const, to: "/notifications" },
  { label: "Settings", shortLabel: "Settings", icon: "settings" as const, to: "/settings" },
];

function NavIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    home: <><path d="m3 11 9-7 9 7" /><path d="M5 10v10h14V10" /><path d="M9 20v-6h6v6" /></>,
    projects: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18" /><path d="M8 4v5" /></>,
    tasks: <><path d="m4 7 2 2 4-4" /><path d="M13 7h7" /><path d="m4 15 2 2 4-4" /><path d="M13 15h7" /></>,
    reminders: <><circle cx="12" cy="13" r="8" /><path d="M12 9v4l3 2" /><path d="M9 3h6" /></>,
    notes: <><path d="M5 3h11l3 3v15H5z" /><path d="M15 3v4h4" /><path d="M8 12h8M8 16h6" /></>,
    lists: <><path d="M9 6h11M9 12h11M9 18h11" /><circle cx="4" cy="6" r="1" /><circle cx="4" cy="12" r="1" /><circle cx="4" cy="18" r="1" /></>,
    activity: <><path d="M4 18V8" /><path d="M10 18V4" /><path d="M16 18v-7" /><path d="M22 18H2" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H3v-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
    more: <><circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" /></>,
    notifications: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M10 21h4" /></>,
    signout: <><path d="M10 17l5-5-5-5" /><path d="M15 12H3" /><path d="M15 4h5v16h-5" /></>,
  };
  return <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

function pageTitle(pathname: string): string {
  if (pathname === "/") return "Mission Control";
  if (pathname.startsWith("/projects/")) return "Project";
  return navigation.find(
    (item) => item.to !== "/" && pathname.startsWith(item.to),
  )?.label
    ?? (pathname.startsWith("/notifications") ? "Notifications" : "Rocky");
}

export default function AppShell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!moreOpen) return;
    const close = (event: PointerEvent) => {
      if (!moreRef.current?.contains(event.target as Node)) setMoreOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMoreOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [moreOpen]);

  useEffect(() => {
    const closeOnNativeBack = (event: Event) => {
      if (!moreOpen) return;
      event.preventDefault();
      setMoreOpen(false);
    };
    window.addEventListener("rocky:native-back", closeOnNativeBack);
    return () => {
      window.removeEventListener("rocky:native-back", closeOnNativeBack);
    };
  }, [moreOpen]);

  const onSignOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="shell">
      <nav className="rail" aria-label="Primary navigation">
        <div className="rail-brand" aria-label="Rocky OS"><BrandMark compact /><span className="rail-brand-word">Rocky</span><small>OS</small></div>
        <div className="rail-nav">
          {navigation.map((item) => (
            <NavLink
              key={item.label}
              to={item.to}
              end={"end" in item ? item.end : undefined}
              className={({ isActive }) => isActive ? "rail-link active" : "rail-link"}
              title={item.label}
            >
              <NavIcon name={item.icon} />
              <span className="rail-link-label">{item.label}</span>
            </NavLink>
          ))}
        </div>
        <div className="rail-account">
          <span className="rail-avatar" aria-hidden="true">R</span>
          <div className="rail-account-copy">
            <strong>Rocky account</strong>
            <button type="button" onClick={onSignOut}>Sign out</button>
          </div>
        </div>
      </nav>
      <header className="mobile-topbar">
        <span className="mobile-brand"><BrandMark compact />Rocky</span>
        <strong>{pageTitle(location.pathname)}</strong>
      </header>
      <main className="content">{children}</main>
      <nav className="mobile-nav" aria-label="Mobile navigation">
        {navigation.filter((item) => mobilePrimary.includes(item.to)).map((item) => (
          <NavLink key={item.to} to={item.to} end={"end" in item ? item.end : undefined} className={({ isActive }) => isActive ? "mobile-nav-link active" : "mobile-nav-link"}>
            <NavIcon name={item.icon} />
            <span>{item.shortLabel}</span>
          </NavLink>
        ))}
        <div className="mobile-more" ref={moreRef}>
          <button className={moreOpen || mobileMore.some((item) => location.pathname.startsWith(item.to)) ? "mobile-nav-link active" : "mobile-nav-link"} type="button" aria-haspopup="menu" aria-expanded={moreOpen} aria-controls="mobile-more-menu" onClick={() => setMoreOpen((open) => !open)}>
            <NavIcon name="more" />
            <span>More</span>
          </button>
          {moreOpen && (
            <div className="mobile-more-menu" id="mobile-more-menu" role="menu">
              {mobileMore.map((item) => (
                <NavLink key={item.to} to={item.to} role="menuitem" onClick={() => setMoreOpen(false)}><NavIcon name={item.icon} /><span>{item.label}</span></NavLink>
              ))}
              <button type="button" role="menuitem" onClick={onSignOut}><NavIcon name="signout" /><span>Sign out</span></button>
            </div>
          )}
        </div>
      </nav>
      <Onboarding />
    </div>
  );
}
