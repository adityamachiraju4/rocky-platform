import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { AuthProvider } from "./auth";
import { useAuth } from "./useAuth";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import PasswordRecoveryPage from "./pages/PasswordRecoveryPage";
import VerifyEmailPage from "./pages/VerifyEmailPage";
import ProjectsPage from "./pages/ProjectsPage";
import MissionControlPage from "./pages/MissionControlPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import ActivityPage from "./pages/ActivityPage";
import TasksPage from "./pages/TasksPage";
import RemindersPage from "./pages/RemindersPage";
import NotificationsPage from "./pages/NotificationsPage";
import NotesPage from "./pages/NotesPage";
import ListsPage from "./pages/ListsPage";
import SettingsPage from "./pages/SettingsPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "booting") return <div className="boot">Loading…</div>;
  if (status === "anon") return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function PublicOnly({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "booting") return <div className="boot"><span className="brand-loader" aria-hidden="true" />Loading Rocky…</div>;
  if (status === "authed") return <Navigate to="/" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route
        path="/login"
        element={<PublicOnly><LoginPage /></PublicOnly>}
      />
      <Route path="/register" element={<PublicOnly><RegisterPage /></PublicOnly>} />
      <Route path="/forgot-password" element={<PublicOnly><PasswordRecoveryPage /></PublicOnly>} />
      <Route path="/reset-password" element={<PublicOnly><PasswordRecoveryPage reset /></PublicOnly>} />
      <Route path="/verify-email" element={<PublicOnly><VerifyEmailPage /></PublicOnly>} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <MissionControlPage />
          </RequireAuth>
        }
      />
      <Route
        path="/projects"
        element={
          <RequireAuth>
            <ProjectsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/projects/:projectId"
        element={
          <RequireAuth>
            <ProjectDetailPage />
          </RequireAuth>
        }
      />
      <Route
        path="/tasks"
        element={
          <RequireAuth>
            <TasksPage />
          </RequireAuth>
        }
      />
      <Route
        path="/reminders"
        element={
          <RequireAuth>
            <RemindersPage />
          </RequireAuth>
        }
      />
      <Route
        path="/notifications"
        element={
          <RequireAuth>
            <NotificationsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/notes"
        element={
          <RequireAuth>
            <NotesPage />
          </RequireAuth>
        }
      />
      <Route
        path="/lists"
        element={
          <RequireAuth>
            <ListsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/activity"
        element={
          <RequireAuth>
            <ActivityPage />
          </RequireAuth>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <SettingsPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
