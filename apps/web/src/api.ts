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
import { apiUrl } from "./apiConfig";
import { ApiError } from "./apiErrors";
import { expectArrayResponse } from "./apiResponse";
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
  TranscriptionResponse,
  Reminder,
  ReminderCreate,
  Notification,
  NotificationUpdate,
  Note,
  NoteCreate,
  NoteUpdate,
  RockyList,
  RockyListCreate,
  RockyListUpdate,
  RockyListItem,
  RockyListItemCreate,
  RockyListItemUpdate,
  UserProfile,
  UserCreate,
  RegisteredUser,
  MessageResponse,
} from "./types";

export { ApiError } from "./apiErrors";

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

function requestUrl(path: string): string {
  try {
    return apiUrl(path);
  } catch (error) {
    throw new ApiError(0, null, error instanceof Error ? error.message : "Invalid API configuration");
  }
}

// Single-flight refresh. Returns true on success (tokens rotated), false otherwise.
async function runRefresh(): Promise<boolean> {
  const refreshToken = await getRefreshToken();
  if (!refreshToken) return false;

  const res = await fetch(requestUrl("/auth/refresh"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!res.ok) {
    await clearTokens();
    return false;
  }

  const tokens = (await res.json()) as TokenResponse;
  setAccessToken(tokens.access_token);
  await setRefreshToken(tokens.refresh_token);
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
  signal?: AbortSignal;
}

export interface BinaryResponse {
  blob: Blob;
  contentType: string;
  status: number;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true, signal } = opts;
  const isFormDataBody = typeof FormData !== "undefined" && body instanceof FormData;

  const doFetch = async (): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (body !== undefined && !isFormDataBody) headers["Content-Type"] = "application/json";
    if (auth) {
      const token = getAccessToken();
      if (token) headers["Authorization"] = `Bearer ${token}`;
    }
    return fetch(requestUrl(path), {
      method,
      headers,
      body: body === undefined
        ? undefined
        : isFormDataBody
          ? body
          : JSON.stringify(body),
      signal,
    });
  };

  let res = await doFetch();

  if (res.status === 401 && auth) {
    const refreshed = await ensureRefresh();
    if (!refreshed) throw new AuthExpiredError();
    res = await doFetch();
    if (res.status === 401) {
      await clearTokens();
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

async function requestArray<T>(path: string, opts: RequestOptions = {}): Promise<T[]> {
  return expectArrayResponse<T>(await request<unknown>(path, opts), path);
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
    return fetch(requestUrl(path), {
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
      await clearTokens();
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
  await setRefreshToken(tokens.refresh_token);
}

export async function register(payload: UserCreate): Promise<RegisteredUser> {
  return request<RegisteredUser>("/identity/users", {
    method: "POST",
    body: payload,
    auth: false,
  });
}

export const requestEmailVerification = (email: string): Promise<MessageResponse> =>
  request<MessageResponse>("/auth/email-verification/request", {
    method: "POST", body: { email }, auth: false,
  });

export const confirmEmailVerification = (token: string): Promise<MessageResponse> =>
  request<MessageResponse>("/auth/email-verification/confirm", {
    method: "POST", body: { token }, auth: false,
  });

export const requestPasswordReset = (email: string): Promise<MessageResponse> =>
  request<MessageResponse>("/auth/password-reset/request", {
    method: "POST", body: { email }, auth: false,
  });

export const confirmPasswordReset = (token: string, newPassword: string): Promise<MessageResponse> =>
  request<MessageResponse>("/auth/password-reset/confirm", {
    method: "POST", body: { token, new_password: newPassword }, auth: false,
  });

export async function logout(): Promise<void> {
  const refreshToken = await getRefreshToken();
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
  await clearTokens();
}

// Boot an authed session from a stored refresh token. Returns true if authed.
export async function bootSession(): Promise<boolean> {
  if (!await getRefreshToken()) return false;
  return ensureRefresh();
}

// ---- Projects -----------------------------------------------------------

export const listProjects = (): Promise<Project[]> => requestArray<Project>("/projects");

export const createProject = (body: ProjectCreate): Promise<Project> =>
  request<Project>("/projects", { method: "POST", body });

export const getProject = (id: string): Promise<Project> =>
  request<Project>(`/projects/${id}`);

export const updateProject = (id: string, body: ProjectUpdate): Promise<Project> =>
  request<Project>(`/projects/${id}`, { method: "PATCH", body });

// ---- Tasks --------------------------------------------------------------

export const listTasks = (projectId: string): Promise<Task[]> =>
  requestArray<Task>(`/projects/${projectId}/tasks`);

export const createTask = (projectId: string, body: TaskCreate): Promise<Task> =>
  request<Task>(`/projects/${projectId}/tasks`, { method: "POST", body });

export const updateTask = (
  projectId: string,
  taskId: string,
  body: TaskUpdate,
): Promise<Task> =>
  request<Task>(`/projects/${projectId}/tasks/${taskId}`, { method: "PATCH", body });

// ---- Activity -----------------------------------------------------------

export const listActivity = (): Promise<Activity[]> => requestArray<Activity>("/activity");

// ---- Conversation -------------------------------------------------------

export const sendConversation = (
  body: ConversationRequest,
  signal?: AbortSignal,
): Promise<ConversationResponse> =>
  request<ConversationResponse>("/conversation", { method: "POST", body, signal });

export const synthesizeSpeech = (
  body: SpeechRequest,
  signal?: AbortSignal,
): Promise<BinaryResponse> =>
  requestBlob("/speech", { method: "POST", body, signal });

export const transcribeAudio = (
  audio: Blob,
  filename = "rocky-voice.webm",
  signal?: AbortSignal,
): Promise<TranscriptionResponse> => {
  const body = new FormData();
  body.append("audio", audio, filename);
  return request<TranscriptionResponse>("/transcribe", { method: "POST", body, signal });
};

// ---- Reminders ----------------------------------------------------------

export const listReminders = (status?: Reminder["status"]): Promise<Reminder[]> =>
  requestArray<Reminder>(status ? `/reminders?status=${encodeURIComponent(status)}` : "/reminders");

export const createReminder = (body: ReminderCreate): Promise<Reminder> =>
  request<Reminder>("/reminders", { method: "POST", body });

export const completeReminder = (id: string): Promise<Reminder> =>
  request<Reminder>(`/reminders/${id}/complete`, { method: "POST" });

export const cancelReminder = (id: string): Promise<Reminder> =>
  request<Reminder>(`/reminders/${id}`, { method: "DELETE" });

// ---- Notifications ------------------------------------------------------

export const listNotifications = (status?: Notification["status"]): Promise<Notification[]> =>
  requestArray<Notification>(status ? `/notifications?status=${encodeURIComponent(status)}` : "/notifications");

export const updateNotification = (
  id: string,
  body: NotificationUpdate,
): Promise<Notification> =>
  request<Notification>(`/notifications/${id}`, { method: "PATCH", body });

// ---- Notes --------------------------------------------------------------

export const listNotes = (status: Note["status"] = "active"): Promise<Note[]> =>
  requestArray<Note>(`/notes?status=${encodeURIComponent(status)}`);

export const getNote = (id: string): Promise<Note> =>
  request<Note>(`/notes/${id}`);

export const createNote = (body: NoteCreate): Promise<Note> =>
  request<Note>("/notes", { method: "POST", body });

export const updateNote = (id: string, body: NoteUpdate): Promise<Note> =>
  request<Note>(`/notes/${id}`, { method: "PATCH", body });

// ---- Lists --------------------------------------------------------------

export const listLists = (status: RockyList["status"] = "active"): Promise<RockyList[]> =>
  requestArray<RockyList>(`/lists?status=${encodeURIComponent(status)}`);

export const getList = (id: string): Promise<RockyList> =>
  request<RockyList>(`/lists/${id}`);

export const createList = (body: RockyListCreate): Promise<RockyList> =>
  request<RockyList>("/lists", { method: "POST", body });

export const updateList = (id: string, body: RockyListUpdate): Promise<RockyList> =>
  request<RockyList>(`/lists/${id}`, { method: "PATCH", body });

export const listListItems = (
  listId: string,
  status?: RockyListItem["status"],
): Promise<RockyListItem[]> =>
  requestArray<RockyListItem>(
    status
      ? `/lists/${listId}/items?status=${encodeURIComponent(status)}`
      : `/lists/${listId}/items`,
  );

export const createListItem = (
  listId: string,
  body: RockyListItemCreate,
): Promise<RockyListItem> =>
  request<RockyListItem>(`/lists/${listId}/items`, { method: "POST", body });

export const updateListItem = (
  listId: string,
  itemId: string,
  body: RockyListItemUpdate,
): Promise<RockyListItem> =>
  request<RockyListItem>(`/lists/${listId}/items/${itemId}`, { method: "PATCH", body });

function authenticatedUserId(): string | null {
  const token = getAccessToken();
  if (!token) return null;
  try {
    const encoded = token.split(".")[1];
    if (!encoded) return null;
    const base64 = encoded.replace(/-/g, "+").replace(/_/g, "/");
    const normalized = base64.padEnd(base64.length + ((4 - base64.length % 4) % 4), "=");
    const payload = JSON.parse(atob(normalized)) as { sub?: unknown };
    return typeof payload.sub === "string" ? payload.sub : null;
  } catch {
    return null;
  }
}

export async function getAuthenticatedProfile(): Promise<UserProfile | null> {
  const userId = authenticatedUserId();
  return userId ? request<UserProfile>(`/identity/users/${userId}`) : null;
}
