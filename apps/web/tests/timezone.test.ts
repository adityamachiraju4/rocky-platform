import assert from "node:assert/strict";
import test from "node:test";

import { normalizeTimezone } from "../src/format.ts";

test("normalizes legacy and canonical Kolkata timezone names", () => {
  assert.equal(normalizeTimezone("Asia/Calcutta"), "Asia/Kolkata");
  assert.equal(normalizeTimezone(" Asia/Kolkata "), "Asia/Kolkata");
});

test("accepts other valid IANA timezone names", () => {
  assert.equal(normalizeTimezone("America/New_York"), "America/New_York");
});

test("rejects invalid timezone names", () => {
  assert.equal(normalizeTimezone("Mars/Olympus"), null);
});
