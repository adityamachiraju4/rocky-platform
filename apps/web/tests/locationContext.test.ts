import assert from "node:assert/strict";
import test from "node:test";

import { needsDeviceLocation } from "../src/locationIntent.ts";

test("nearby live-intelligence requests ask for device location", () => {
  assert.equal(needsDeviceLocation("restaurants near me"), true);
  assert.equal(needsDeviceLocation("What's the weather today?"), true);
});

test("explicit locations do not ask for device location", () => {
  assert.equal(needsDeviceLocation("What's the weather in Hyderabad?"), false);
  assert.equal(needsDeviceLocation("coffee shops in London"), false);
});
