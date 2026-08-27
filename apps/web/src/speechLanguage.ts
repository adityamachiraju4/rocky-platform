const REGIONAL_SPEECH_LANGUAGES: Readonly<Record<string, string>> = {
  ta: "ta-IN",
};

export function browserSpeechLanguage(language: string): string {
  const normalized = language.trim().replace("_", "-");
  const primary = normalized.toLowerCase().split("-", 1)[0];
  return REGIONAL_SPEECH_LANGUAGES[primary] ?? normalized;
}
