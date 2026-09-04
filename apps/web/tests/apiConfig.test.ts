import assert from "node:assert/strict";
import test from "node:test";

import { apiBaseUrl, apiUrl, PRODUCTION_API_BASE } from "../src/apiConfig.ts";

test("API base defaults to the existing proxied web path", () => {
  assert.equal(apiBaseUrl(undefined, { native: false, prod: false }), "/api");
  assert.equal(apiBaseUrl("", { native: false, prod: false }), "/api");
});

test("API base accepts deployed HTTPS backends without a trailing slash", () => {
  assert.equal(apiBaseUrl("https://api.rocky.example/", { native: false }), "https://api.rocky.example");
});

test("native API base refuses the web default instead of using Capacitor localhost", () => {
  assert.throws(
    () => apiBaseUrl(undefined, { native: true, mode: "native", prod: true }),
    /Native API base requires VITE_API_BASE_URL/,
  );
  assert.throws(
    () => apiBaseUrl("/api", { native: true }),
    /Native API base must be an absolute Rocky backend URL/,
  );
  assert.throws(
    () => apiBaseUrl("https://localhost/api", { native: true }),
    /must not use Capacitor's local https:\/\/localhost WebView origin/,
  );
});

test("production web defaults to the deployed Railway API", () => {
  assert.equal(apiBaseUrl(undefined, { native: false, prod: true }), PRODUCTION_API_BASE);
});

test("native release defaults to the deployed Railway API", () => {
  assert.equal(
    apiBaseUrl(undefined, { native: true, mode: "native-release", prod: true }),
    PRODUCTION_API_BASE,
  );
});

test("Android emulator native API base remains explicit", () => {
  assert.equal(
    apiBaseUrl("https://10.0.2.2:8000", { native: true, mode: "native", prod: true }),
    "https://10.0.2.2:8000",
  );
});

test("explicit absolute native API base joins endpoints correctly", () => {
  const base = apiBaseUrl("https://api.rocky.example/api/", { native: true });

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
  assert.equal(apiUrl("/projects", apiBaseUrl(undefined, { native: false, prod: false })), "/api/projects");
});
