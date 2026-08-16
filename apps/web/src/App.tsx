import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { AuthProvider } from "./auth";
import { useAuth } from "./useAuth";
import LoginPage from "./pages/LoginPage";
import ProjectsPage from "./pages/ProjectsPage";
import MissionControlPage from "./pages/MissionControlPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import ActivityPage from "./pages/ActivityPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "booting") return <div className="boot">Loading…</div>;
  if (status === "anon") return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  const { status } = useAuth();
  return (
    <Routes>
      <Route
        path="/login"
        element={status === "authed" ? <Navigate to="/" replace /> : <LoginPage />}
      />
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
        path="/activity"
        element={
          <RequireAuth>
            <ActivityPage />
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
