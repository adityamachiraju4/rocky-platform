import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, apiErrorCode } from "../src/apiErrors.ts";
import { loginErrorMessage, registrationErrorMessage } from "../src/authMessages.ts";

test("login errors do not expose raw backend details", () => {
  assert.equal(
    loginErrorMessage(new ApiError(401, { detail: "internal" }, "raw backend text")),
    "That email and password combination doesn’t match.",
  );
  assert.equal(
    loginErrorMessage(new ApiError(500, { detail: "database unavailable" }, "database unavailable")),
    "Rocky’s sign-in service is temporarily unavailable. Please try again shortly.",
  );
});

test("login network failures are distinct from server failures", () => {
  assert.equal(
    loginErrorMessage(new TypeError("Failed to fetch")),
    "Rocky can’t reach the server right now. Check your connection and try again.",
  );
  assert.equal(
    loginErrorMessage(new ApiError(503, null, "Service unavailable")),
    "Rocky’s sign-in service is temporarily unavailable. Please try again shortly.",
  );
});

test("verification and duplicate registration have actionable copy", () => {
  assert.match(
    loginErrorMessage(new ApiError(403, { detail: { code: "EMAIL_NOT_VERIFIED" } }, "Forbidden")),
    /verified/,
  );
  assert.equal(
    registrationErrorMessage(new ApiError(409, null, "Email already registered")),
    "An account already exists for that email.",
  );
});

test("structured backend error codes are available without parsing messages", () => {
  assert.equal(
    apiErrorCode(new ApiError(403, { detail: { code: "EMAIL_NOT_VERIFIED" } }, "Forbidden")),
    "EMAIL_NOT_VERIFIED",
  );
  assert.equal(apiErrorCode(new Error("EMAIL_NOT_VERIFIED")), null);
});
