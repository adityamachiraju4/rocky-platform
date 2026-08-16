// Token storage: access token in memory (dies on reload, re-minted via refresh),
// refresh token in localStorage (must survive reload; no httpOnly-cookie path from
// the backend). localStorage carries XSS exposure — accepted tradeoff, recorded as
// a finding. The access token is deliberately NOT persisted.

const REFRESH_KEY = "rocky.refresh_token";

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

export function setRefreshToken(token: string | null): void {
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

export function clearTokens(): void {
  accessToken = null;
  setRefreshToken(null);
}
