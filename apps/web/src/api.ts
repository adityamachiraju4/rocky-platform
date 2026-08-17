// Typed fetch wrapper against the backend, proxied at /api in dev.
//
// Auth flow:
//  - Access token (in memory) is attached as Bearer on every request.
//  - On 401, a single-flight refresh runs: the first 401 kicks off /auth/refresh,
//    concurrent 401s await the same in-flight promise, then all retry once.
//  - Rotation: /auth/refresh returns a NEW access+refresh pair; both are stored.
//  - If refresh fails, tokens are cleared and the caller sees AuthExpiredError.

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
} from "./tokenStore";
import type {
  Activity,
  ConversationRequest,
  ConversationResponse,
  LoginRequest,
  Project,
  ProjectCreate,
  ProjectUpdate,
  Task,
  TaskCreate,
  TaskUpdate,
  TokenResponse,
  SpeechRequest,
} from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// Raised when refresh has failed and the session is unrecoverable.
export class AuthExpiredError extends Error {
  constructor() {
    super("Session expired");
    this.name = "AuthExpiredError";
  }
}

let refreshPromise: Promise<boolean> | null = null;

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "object" && detail !== null && "detail" in detail) {
    const d = (detail as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return fallback;
}

// Single-flight refresh. Returns true on success (tokens rotated), false otherwise.
async function runRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;

  const res = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!res.ok) {
    clearTokens();
    return false;
  }

  const tokens = (await res.json()) as TokenResponse;
  setAccessToken(tokens.access_token);
  setRefreshToken(tokens.refresh_token);
  return true;
}

function ensureRefresh(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = runRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  auth?: boolean; // attach bearer + participate in refresh-on-401 (default true)
}

export interface BinaryResponse {
  blob: Blob;
  contentType: string;
  status: number;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true } = opts;

  const doFetch = async (): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth) {
      const token = getAccessToken();
      if (token) headers["Authorization"] = `Bearer ${token}`;
    }
    return fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  };

  let res = await doFetch();

  if (res.status === 401 && auth) {
    const refreshed = await ensureRefresh();
    if (!refreshed) throw new AuthExpiredError();
    res = await doFetch();
    if (res.status === 401) {
      clearTokens();
      throw new AuthExpiredError();
    }
  }

  if (res.status === 204) return undefined as T;

  const parsed = await parseBody(res);
  if (!res.ok) {
    throw new ApiError(res.status, parsed, detailMessage(parsed, `HTTP ${res.status}`));
  }
  return parsed as T;
}

async function requestBlob(
  path: string,
  opts: RequestOptions & { signal?: AbortSignal } = {},
): Promise<BinaryResponse> {
  const { method = "GET", body, auth = true, signal } = opts;

  const doFetch = async (): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth) {
      const token = getAccessToken();
      if (token) headers["Authorization"] = `Bearer ${token}`;
    }
    return fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  };

  let res = await doFetch();

  if (res.status === 401 && auth) {
    const refreshed = await ensureRefresh();
    if (!refreshed) throw new AuthExpiredError();
    res = await doFetch();
    if (res.status === 401) {
      clearTokens();
      throw new AuthExpiredError();
    }
  }

  if (!res.ok) {
    const parsed = await parseBody(res);
    throw new ApiError(res.status, parsed, detailMessage(parsed, `HTTP ${res.status}`));
  }

  return {
    blob: await res.blob(),
    contentType: (res.headers.get("content-type") ?? "").split(";")[0],
    status: res.status,
  };
}

// ---- Auth ---------------------------------------------------------------

export async function login(payload: LoginRequest): Promise<void> {
  const tokens = await request<TokenResponse>("/auth/login", {
    method: "POST",
    body: payload,
    auth: false,
  });
  setAccessToken(tokens.access_token);
  setRefreshToken(tokens.refresh_token);
}

export async function logout(): Promise<void> {
  const refreshToken = getRefreshToken();
  if (refreshToken) {
    try {
      await request<void>("/auth/logout", {
        method: "POST",
        body: { refresh_token: refreshToken },
        auth: false,
      });
    } catch {
      /* logout is best-effort; clear locally regardless */
    }
  }
  clearTokens();
}

// Boot an authed session from a stored refresh token. Returns true if authed.
export async function bootSession(): Promise<boolean> {
  if (!getRefreshToken()) return false;
  return ensureRefresh();
}

// ---- Projects -----------------------------------------------------------

export const listProjects = (): Promise<Project[]> => request<Project[]>("/projects");

export const createProject = (body: ProjectCreate): Promise<Project> =>
  request<Project>("/projects", { method: "POST", body });

export const getProject = (id: string): Promise<Project> =>
  request<Project>(`/projects/${id}`);

export const updateProject = (id: string, body: ProjectUpdate): Promise<Project> =>
  request<Project>(`/projects/${id}`, { method: "PATCH", body });

// ---- Tasks --------------------------------------------------------------

export const listTasks = (projectId: string): Promise<Task[]> =>
  request<Task[]>(`/projects/${projectId}/tasks`);

export const createTask = (projectId: string, body: TaskCreate): Promise<Task> =>
  request<Task>(`/projects/${projectId}/tasks`, { method: "POST", body });

export const updateTask = (
  projectId: string,
  taskId: string,
  body: TaskUpdate,
): Promise<Task> =>
  request<Task>(`/projects/${projectId}/tasks/${taskId}`, { method: "PATCH", body });

// ---- Activity -----------------------------------------------------------

export const listActivity = (): Promise<Activity[]> => request<Activity[]>("/activity");

// ---- Conversation -------------------------------------------------------

export const sendConversation = (
  body: ConversationRequest,
): Promise<ConversationResponse> =>
  request<ConversationResponse>("/conversation", { method: "POST", body });

export const synthesizeSpeech = (
  body: SpeechRequest,
  signal?: AbortSignal,
): Promise<BinaryResponse> =>
  requestBlob("/speech", { method: "POST", body, signal });
