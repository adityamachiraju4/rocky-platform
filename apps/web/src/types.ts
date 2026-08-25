// Mirrors the backend Pydantic read schemas byte-for-byte.
// Timestamps arrive as ISO strings over the wire.

export type UUID = string;
export type ISODateTime = string;

export type TaskStatus = "active" | "complete";

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface LoginRequest {
  email: string;
  password: string;
  client_id?: UUID;
  device_name?: string;
  device_type?: string;
  platform?: string;
}

export interface Project {
  id: UUID;
  user_id: UUID;
  name: string;
  description: string | null;
  status: string;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface ProjectCreate {
  name: string;
  description?: string | null;
  status?: string;
}

export interface ProjectUpdate {
  name?: string | null;
  description?: string | null;
  status?: string | null;
}

export interface Task {
  id: UUID;
  project_id: UUID;
  title: string;
  description: string | null;
  status: string;
  completed_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface TaskCreate {
  title: string;
  description?: string | null;
  status?: TaskStatus;
}

export interface TaskUpdate {
  title?: string | null;
  description?: string | null;
  status?: TaskStatus | null;
}

export interface Activity {
  id: UUID;
  user_id: UUID;
  event_type: string;
  entity_type: string;
  entity_id: UUID;
  payload: Record<string, unknown>;
  created_at: ISODateTime;
}

export interface ConversationRequest {
  message: string;
  timezone?: string | null;
}

export interface ConversationResponse {
  executed: boolean;
  action: string | null;
  reply: string;
}

export interface SpeechRequest {
  text: string;
}

export interface TranscriptionResponse {
  text: string;
}

export interface UserProfile {
  id: UUID;
  email: string;
  full_name: string | null;
  timezone: string;
}

export interface Reminder {
  id: UUID;
  user_id: UUID;
  title: string;
  notes: string | null;
  due_at: ISODateTime;
  timezone: string;
  status: "scheduled" | "due" | "completed" | "cancelled";
  triggered_at: ISODateTime | null;
  completed_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface ReminderCreate {
  title: string;
  notes?: string | null;
  due_at: ISODateTime;
  timezone?: string | null;
  idempotency_key?: string | null;
}

export interface Notification {
  id: UUID;
  user_id: UUID;
  type: string;
  title: string;
  body: string;
  status: "unread" | "read" | "dismissed";
  source_type: string | null;
  source_id: UUID | null;
  source_metadata: Record<string, unknown>;
  read_at: ISODateTime | null;
  dismissed_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface NotificationUpdate {
  status: "read" | "dismissed";
}

export interface Note {
  id: UUID;
  user_id: UUID;
  title: string;
  content: string;
  status: "active" | "archived";
  created_at: ISODateTime;
  updated_at: ISODateTime;
  archived_at: ISODateTime | null;
}

export interface NoteCreate {
  title: string;
  content?: string;
}

export interface NoteUpdate {
  title?: string;
  content?: string;
  status?: "archived";
}

export interface RockyList {
  id: UUID;
  user_id: UUID;
  title: string;
  status: "active" | "archived";
  created_at: ISODateTime;
  updated_at: ISODateTime;
  archived_at: ISODateTime | null;
}

export interface RockyListCreate {
  title: string;
}

export interface RockyListUpdate {
  title?: string;
  status?: "archived";
}

export interface RockyListItem {
  id: UUID;
  list_id: UUID;
  content: string;
  status: "active" | "complete";
  position: number;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  completed_at: ISODateTime | null;
}

export interface RockyListItemCreate {
  content: string;
}

export interface RockyListItemUpdate {
  content?: string;
  status?: "complete";
}
