// Shared auth context object. Split out so both the provider component
// (auth.tsx) and the useAuth hook (useAuth.ts) can import it without any
// single file exporting both a component and a non-component (react-refresh).

import { createContext } from "react";
import type { LoginRequest } from "./types";

export type AuthStatus = "booting" | "authed" | "anon";

export interface AuthContextValue {
  status: AuthStatus;
  login: (payload: LoginRequest) => Promise<void>;
  logout: () => Promise<void>;
  onExpired: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
