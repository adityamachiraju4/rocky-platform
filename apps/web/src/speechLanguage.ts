const REGIONAL_SPEECH_LANGUAGES: Readonly<Record<string, string>> = {
  en: "en-US",
  hi: "hi-IN",
  te: "te-IN",
  ta: "ta-IN",
  es: "es-ES",
};

export function browserSpeechLanguage(language: string): string {
  const normalized = language.trim().replace("_", "-") || "en";
  const primary = normalized.toLowerCase().split("-", 1)[0];
  return REGIONAL_SPEECH_LANGUAGES[primary] ?? normalized;
}
