import assert from "node:assert/strict";
import test from "node:test";

import { browserSpeechLanguage } from "../src/speechLanguage.ts";

test("Tamil browser speech uses the regional voice tag", () => {
  assert.equal(browserSpeechLanguage("ta"), "ta-IN");
  assert.equal(browserSpeechLanguage("ta_IN"), "ta-IN");
  assert.equal(browserSpeechLanguage("ta-IN"), "ta-IN");
});

test("browser speech maps supported language codes to regional voice tags", () => {
  assert.equal(browserSpeechLanguage("hi"), "hi-IN");
  assert.equal(browserSpeechLanguage("te"), "te-IN");
  assert.equal(browserSpeechLanguage("es"), "es-ES");
});

test("unknown and already regional browser speech tags remain unchanged", () => {
  assert.equal(browserSpeechLanguage("en"), "en");
  assert.equal(browserSpeechLanguage("te-IN"), "te-IN");
});
