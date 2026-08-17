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
