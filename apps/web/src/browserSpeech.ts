import { browserSpeechLanguage } from "./speechLanguage.ts";

const DEFAULT_VOICE_READY_TIMEOUT_MS = 1_500;

export type BrowserSpeechStartResult = "started" | "skipped" | "failed";

export interface BrowserSpeechEngine {
  readonly paused: boolean;
  readonly pending: boolean;
  readonly speaking: boolean;
  addEventListener(type: "voiceschanged", listener: () => void): void;
  removeEventListener(type: "voiceschanged", listener: () => void): void;
  getVoices(): SpeechSynthesisVoice[];
  cancel(): void;
  speak(utterance: SpeechSynthesisUtterance): void;
}

function engineActive(engine: BrowserSpeechEngine): boolean {
  return engine.speaking || engine.pending || engine.paused;
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

interface StartBrowserSpeechOptions {
  engine: BrowserSpeechEngine;
  utterance: SpeechSynthesisUtterance;
  language: string;
  enabled: boolean;
  isCurrent: () => boolean;
  voiceReadyTimeoutMs?: number;
}

export async function waitForBrowserVoices(
  engine: BrowserSpeechEngine,
  timeoutMs = DEFAULT_VOICE_READY_TIMEOUT_MS,
): Promise<SpeechSynthesisVoice[]> {
  const available = engine.getVoices();
  if (available.length > 0) return available;

  return new Promise((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const finish = (voices: SpeechSynthesisVoice[]) => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      engine.removeEventListener("voiceschanged", onVoicesChanged);
      resolve(voices);
    };
    const onVoicesChanged = () => {
      const voices = engine.getVoices();
      if (voices.length > 0) finish(voices);
    };

    engine.addEventListener("voiceschanged", onVoicesChanged);
    timer = setTimeout(() => finish(engine.getVoices()), timeoutMs);
    onVoicesChanged();
  });
}

export function selectBrowserVoice(
  voices: readonly SpeechSynthesisVoice[],
  language: string,
): SpeechSynthesisVoice | null {
  const requested = browserSpeechLanguage(language).toLowerCase();
  const primary = requested.split("-", 1)[0];
  return voices.find((voice) => voice.lang.toLowerCase() === requested)
    ?? voices.find((voice) => voice.lang.toLowerCase().split("-", 1)[0] === primary)
    ?? voices.find((voice) => voice.default)
    ?? voices[0]
    ?? null;
}

export async function startBrowserSpeech({
  engine,
  utterance,
  language,
  enabled,
  isCurrent,
  voiceReadyTimeoutMs,
}: StartBrowserSpeechOptions): Promise<BrowserSpeechStartResult> {
  if (!enabled || !isCurrent()) return "skipped";

  const speechLanguage = browserSpeechLanguage(language);
  utterance.lang = speechLanguage;
  const voice = selectBrowserVoice(
    await waitForBrowserVoices(engine, voiceReadyTimeoutMs),
    speechLanguage,
  );
  if (!enabled || !isCurrent()) return "skipped";
  if (voice) utterance.voice = voice;

  try {
    if (engineActive(engine)) {
      engine.cancel();
      await nextTask();
      if (!enabled || !isCurrent()) return "skipped";
    }
    engine.speak(utterance);
    return "started";
  } catch {
    return "failed";
  }
}
