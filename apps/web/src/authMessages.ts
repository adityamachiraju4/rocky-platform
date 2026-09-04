import { ApiError, apiErrorCode } from "./apiErrors.ts";

export function loginErrorMessage(error: unknown): string {
  if (error instanceof TypeError) return "Rocky can’t reach the server right now. Check your connection and try again.";
  if (!(error instanceof ApiError)) return "Rocky couldn’t sign you in. Please try again.";
  if (error.status === 401) return "That email and password combination doesn’t match.";
  if (error.status === 403) {
    return apiErrorCode(error) === "EMAIL_NOT_VERIFIED"
      ? "Your email needs to be verified before you can sign in."
      : "This account is not currently available.";
  }
  if (error.status === 422) return "Check your email and password, then try again.";
  if (error.status === 0) return "Rocky can’t reach the server right now. Check your connection and try again.";
  if (error.status >= 500) return "Rocky’s sign-in service is temporarily unavailable. Please try again shortly.";
  return "Rocky couldn’t sign you in. Please try again.";
}

export function registrationErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Rocky couldn’t create your account. Please try again.";
  if (error.status === 409) return "An account already exists for that email.";
  if (error.status === 422) return "Check the highlighted details and try again.";
  if (error.status === 0) return "Rocky can’t reach the server right now. Check your connection and try again.";
  return "Rocky couldn’t create your account. Please try again.";
}
