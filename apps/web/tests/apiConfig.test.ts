import assert from "node:assert/strict";
import test from "node:test";

import { apiBaseUrl, apiUrl, PRODUCTION_API_BASE } from "../src/apiConfig.ts";

test("API base defaults to the existing proxied web path", () => {
  assert.equal(apiBaseUrl(undefined, { prod: false }), "/api");
  assert.equal(apiBaseUrl("", { prod: false }), "/api");
});

test("API base accepts deployed HTTPS backends without a trailing slash", () => {
  assert.equal(apiBaseUrl("https://api.rocky.example/"), "https://api.rocky.example");
});

test("production web defaults to the deployed Railway API", () => {
  assert.equal(apiBaseUrl(undefined, { prod: true }), PRODUCTION_API_BASE);
});

test("absolute API bases join endpoints correctly", () => {
  const base = apiBaseUrl("https://api.rocky.example/api/");

  assert.equal(apiUrl("/projects", base), "https://api.rocky.example/api/projects");
  assert.equal(apiUrl("projects", base), "https://api.rocky.example/api/projects");
  assert.equal(
    apiUrl("/reminders?status=scheduled", base),
    "https://api.rocky.example/api/reminders?status=scheduled",
  );
  assert.throws(
    () => apiUrl("/api/projects", base),
    /must not include \/api when the API base already includes \/api/,
  );
});

test("web API base joins endpoints without changing PWA behavior", () => {
  assert.equal(apiUrl("/projects", apiBaseUrl(undefined, { prod: false })), "/api/projects");
});
