import { NavLink, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./useAuth";

const navigation = [
  { label: "Mission Control", icon: "⌂", to: "/", end: true },
  { label: "Projects", icon: "□", to: "/projects" },
  { label: "Tasks", icon: "✓", to: "/tasks" },
  { label: "Reminders", icon: "◷", to: "/reminders" },
  { label: "Notes", icon: "✎", to: "/notes" },
  { label: "Lists", icon: "≡", to: "/lists" },
  { label: "Activity", icon: "↗", to: "/activity" },
  { label: "Settings", icon: "⚙", to: "/settings" },
] as const;

export default function AppShell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const navigate = useNavigate();

  const onSignOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="shell">
      <nav className="rail" aria-label="Primary navigation">
        <div className="rail-brand" aria-label="Rocky">
          <span className="rail-brand-word">ROCKY</span>
        </div>
        <div className="rail-nav">
          {navigation.map((item) => (
            <NavLink
              key={item.label}
              to={item.to}
              end={"end" in item ? item.end : undefined}
              className={({ isActive }) => isActive ? "rail-link active" : "rail-link"}
            >
              <span className="rail-icon" aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
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
      <main className="content">{children}</main>
    </div>
  );
}
