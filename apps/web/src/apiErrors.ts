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

export function apiErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError) || typeof error.detail !== "object" || error.detail === null) return null;
  const detail = "detail" in error.detail ? (error.detail as { detail: unknown }).detail : null;
  if (typeof detail !== "object" || detail === null || !("code" in detail)) return null;
  return typeof (detail as { code: unknown }).code === "string" ? (detail as { code: string }).code : null;
}
