const LOCATION_CUES = [
  /\bnear me\b/i,
  /\bnearby\b/i,
  /\bmy location\b/i,
  /\bwhere i am\b/i,
  /\bweather (?:today|now|tomorrow|tonight)\b/i,
  /\bwhat(?:'s| is) the weather\b/i,
  /\brestaurants?\b/i,
  /\bcoffee\b/i,
  /\bcafes?\b/i,
  /\bhospitals?\b/i,
];

const EXPLICIT_LOCATION_CUE = /\b(?:in|for|at)\s+[A-Za-z][A-Za-z .'-]{1,80}/i;

export function needsDeviceLocation(message: string): boolean {
  return LOCATION_CUES.some((pattern) => pattern.test(message))
    && !EXPLICIT_LOCATION_CUE.test(message);
}
