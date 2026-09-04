// Token storage: access token in memory (dies on reload, re-minted via refresh).
// Refresh-token persistence is abstracted so web/PWA can keep the existing
// localStorage behavior while Capacitor native can use its native storage bridge.

import { Preferences } from "@capacitor/preferences";
import { isNativeApp } from "./native";

const REFRESH_KEY = "rocky.refresh_token";

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export async function getRefreshToken(): Promise<string | null> {
  if (isNativeApp()) {
    try {
      return (await Preferences.get({ key: REFRESH_KEY })).value;
    } catch {
      return null;
    }
  }
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

export async function setRefreshToken(token: string | null): Promise<void> {
  if (isNativeApp()) {
    try {
      if (token === null) {
        await Preferences.remove({ key: REFRESH_KEY });
      } else {
        await Preferences.set({ key: REFRESH_KEY, value: token });
      }
    } catch {
      /* native storage unavailable; session becomes memory-only */
    }
    return;
  }
  try {
    if (token === null) {
      localStorage.removeItem(REFRESH_KEY);
    } else {
      localStorage.setItem(REFRESH_KEY, token);
    }
  } catch {
    /* storage unavailable (private mode); session becomes memory-only */
  }
}

export async function clearTokens(): Promise<void> {
  accessToken = null;
  await setRefreshToken(null);
}
