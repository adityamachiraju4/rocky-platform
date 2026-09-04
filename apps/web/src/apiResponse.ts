import { ApiError } from "./apiErrors.ts";

export function expectArrayResponse<T>(value: unknown, endpoint: string): T[] {
  if (Array.isArray(value)) return value as T[];
  const shape = value === null ? "null" : typeof value;
  throw new ApiError(
    200,
    value,
    `Malformed response from ${endpoint}: expected array, received ${shape}`,
  );
}
