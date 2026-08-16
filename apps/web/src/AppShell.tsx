import { NavLink, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./useAuth";

export default function AppShell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const navigate = useNavigate();

  const onSignOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="shell">
      <nav className="rail">
        <div className="rail-brand">Rocky</div>
        <div className="rail-nav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "rail-link active" : "rail-link")}>
            Mission Control
          </NavLink>
          <NavLink to="/projects" className={({ isActive }) => (isActive ? "rail-link active" : "rail-link")}>
            Projects
          </NavLink>
          <NavLink to="/activity" className={({ isActive }) => (isActive ? "rail-link active" : "rail-link")}>
            Activity
          </NavLink>
        </div>
        <button className="rail-signout" onClick={onSignOut}>
          Sign out
        </button>
      </nav>
      <main className="content">{children}</main>
    </div>
  );
}
