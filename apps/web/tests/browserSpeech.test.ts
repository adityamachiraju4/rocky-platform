import assert from "node:assert/strict";
import test from "node:test";

import {
  startBrowserSpeech,
  type BrowserSpeechEngine,
} from "../src/browserSpeech.ts";

type Voice = SpeechSynthesisVoice;

function voice(lang: string, name = lang): Voice {
  return { default: false, lang, localService: true, name, voiceURI: name };
}

function utterance(): SpeechSynthesisUtterance {
  return { lang: "", voice: null } as SpeechSynthesisUtterance;
}

class FakeSpeechEngine implements BrowserSpeechEngine {
  voices: Voice[] = [];
  spoken: SpeechSynthesisUtterance[] = [];
  cancelled = 0;
  failOnSpeak = false;
  paused = false;
  pending = false;
  speaking = false;
  private listeners = new Set<() => void>();

  addEventListener(_type: "voiceschanged", listener: () => void): void {
    this.listeners.add(listener);
  }

  removeEventListener(_type: "voiceschanged", listener: () => void): void {
    this.listeners.delete(listener);
  }

  getVoices(): Voice[] {
    return this.voices;
  }

  cancel(): void {
    this.cancelled += 1;
    this.paused = false;
    this.pending = false;
    this.speaking = false;
  }

  speak(value: SpeechSynthesisUtterance): void {
    if (this.failOnSpeak) throw new Error("speech failed");
    this.spoken.push(value);
  }

  loadVoices(voices: Voice[]): void {
    this.voices = voices;
    for (const listener of this.listeners) listener();
  }
}

test("a backend 503 falls back to an English utterance passed to speak", async () => {
  const engine = new FakeSpeechEngine();
  const value = utterance();
  const backendSpeech = Promise.reject(new Error("503 Service Unavailable"));
  const started = backendSpeech.catch(() => startBrowserSpeech({
      engine,
      utterance: value,
      language: "en",
      enabled: true,
      isCurrent: () => true,
      voiceReadyTimeoutMs: 100,
    }));

  assert.equal(engine.spoken.length, 0);
  engine.loadVoices([voice("hi-IN", "Hindi"), voice("en-US", "English US")]);

  assert.equal(await started, "started");
  assert.equal(value.lang, "en-US");
  assert.equal(value.voice?.name, "English US");
  assert.equal(engine.spoken.length, 1);
  assert.equal(engine.spoken[0], value);
  assert.equal(engine.cancelled, 0);
});

test("English fallback prefers en-US over an earlier English voice", async () => {
  const engine = new FakeSpeechEngine();
  engine.voices = [voice("en-GB", "English UK"), voice("en-US", "English US")];
  const value = utterance();

  assert.equal(await startBrowserSpeech({
    engine,
    utterance: value,
    language: "en",
    enabled: true,
    isCurrent: () => true,
  }), "started");
  assert.equal(value.lang, "en-US");
  assert.equal(value.voice?.name, "English US");
});

test("fallback selects the default voice when no English voice exists", async () => {
  const engine = new FakeSpeechEngine();
  const defaultVoice = voice("fr-FR", "System Default");
  Object.defineProperty(defaultVoice, "default", { value: true });
  engine.voices = [voice("hi-IN", "Hindi"), defaultVoice];
  const value = utterance();

  assert.equal(await startBrowserSpeech({
    engine,
    utterance: value,
    language: "en",
    enabled: true,
    isCurrent: () => true,
  }), "started");
  assert.equal(value.voice?.name, "System Default");
});

test("Replay starts one new utterance after voices are ready", async () => {
  const engine = new FakeSpeechEngine();
  engine.loadVoices([voice("te-IN", "Telugu")]);

  for (let attempt = 0; attempt < 2; attempt += 1) {
    assert.equal(await startBrowserSpeech({
      engine,
      utterance: utterance(),
      language: "te",
      enabled: true,
      isCurrent: () => true,
    }), "started");
  }

  assert.equal(engine.spoken.length, 2);
  assert.equal(engine.cancelled, 0);
});

test("Spoken Replies off skips browser speech", async () => {
  const engine = new FakeSpeechEngine();

  assert.equal(await startBrowserSpeech({
    engine,
    utterance: utterance(),
    language: "ta",
    enabled: false,
    isCurrent: () => true,
  }), "skipped");
  assert.equal(engine.spoken.length, 0);
  assert.equal(engine.cancelled, 0);
});

test("a stale fallback does not speak after delayed voice readiness", async () => {
  const engine = new FakeSpeechEngine();
  let current = true;
  const started = startBrowserSpeech({
    engine,
    utterance: utterance(),
    language: "es",
    enabled: true,
    isCurrent: () => current,
    voiceReadyTimeoutMs: 100,
  });

  current = false;
  engine.loadVoices([voice("es-ES")]);
  assert.equal(await started, "skipped");
  assert.equal(engine.spoken.length, 0);
});

test("an empty voice registry is bounded and still attempts speech", async () => {
  const engine = new FakeSpeechEngine();
  const value = utterance();

  assert.equal(await startBrowserSpeech({
    engine,
    utterance: value,
    language: "es",
    enabled: true,
    isCurrent: () => true,
    voiceReadyTimeoutMs: 1,
  }), "started");
  assert.equal(value.lang, "es-ES");
  assert.equal(value.voice, null);
  assert.equal(engine.spoken.length, 1);
});

test("browser speech start failure is reported without retrying", async () => {
  const engine = new FakeSpeechEngine();
  engine.voices = [voice("ta-IN")];
  engine.failOnSpeak = true;

  assert.equal(await startBrowserSpeech({
    engine,
    utterance: utterance(),
    language: "ta",
    enabled: true,
    isCurrent: () => true,
  }), "failed");
  assert.equal(engine.spoken.length, 0);
  assert.equal(engine.cancelled, 0);
});

test("active speech cancellation settles in a new task before speak", async () => {
  class CancelRaceEngine extends FakeSpeechEngine {
    cancellationPending = false;

    override cancel(): void {
      super.cancel();
      this.cancellationPending = true;
      setTimeout(() => {
        this.cancellationPending = false;
      }, 0);
    }

    override speak(value: SpeechSynthesisUtterance): void {
      if (!this.cancellationPending) super.speak(value);
    }
  }

  const engine = new CancelRaceEngine();
  engine.voices = [voice("hi-IN")];
  engine.speaking = true;

  assert.equal(await startBrowserSpeech({
    engine,
    utterance: utterance(),
    language: "hi",
    enabled: true,
    isCurrent: () => true,
  }), "started");
  assert.equal(engine.cancelled, 1);
  assert.equal(engine.spoken.length, 1);
});
