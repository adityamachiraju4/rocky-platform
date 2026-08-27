import assert from "node:assert/strict";
import test from "node:test";

import { browserSpeechLanguage } from "../src/speechLanguage.ts";

test("Tamil browser speech uses the regional voice tag", () => {
  assert.equal(browserSpeechLanguage("ta"), "ta-IN");
  assert.equal(browserSpeechLanguage("ta_IN"), "ta-IN");
  assert.equal(browserSpeechLanguage("ta-IN"), "ta-IN");
});

test("other browser speech language tags remain unchanged", () => {
  assert.equal(browserSpeechLanguage("en"), "en");
  assert.equal(browserSpeechLanguage("te-IN"), "te-IN");
});
