export const SPEECH_LEVEL_THRESHOLD = 0.035;
export const SILENCE_STOP_MS = 900;
export const MIN_RECORDING_MS = 700;
export const MAX_RECORDING_MS = 30_000;

export interface VoiceActivityConfig {
  speechLevelThreshold: number;
  calibratedSpeechLevelThreshold: number;
  silenceStopMs: number;
  minRecordingMs: number;
  initialCalibrationMs: number;
  ambientSmoothing: number;
  speechAmbientMultiplier: number;
  speechAmbientMargin: number;
  silenceAmbientMultiplier: number;
  silenceAmbientMargin: number;
}

export interface VoiceActivityState {
  ambientLevel: number | null;
  peakLevel: number;
  peakElapsedMs: number | null;
  speechStartThresholdAtPeak: number | null;
  speechDetected: boolean;
  silenceStartedAt: number | null;
  calibrated: boolean;
}

export interface VoiceActivityDecision {
  ambientLevel: number;
  level: number;
  silenceStartedAt: number | null;
  silenceThreshold: number;
  speechDetected: boolean;
  speechStartThreshold: number;
  shouldStop: boolean;
}

export interface VoiceActivityInspection {
  ambientLevel: number;
  calibrated: boolean;
  level: number;
  silenceThreshold: number;
  speechDetected: boolean;
  speechStartThreshold: number;
}

export type RecordingRejectionReason =
  | "no-speech-detected"
  | "too-short"
  | "no-recorder-chunks"
  | "zero-byte-blob";

export interface RecordingValidationInput {
  speechDetected: boolean;
  durationMs: number;
  minRecordingMs: number;
  chunkCount: number;
  blobSize: number;
}

export interface RawSignalStats {
  nonZeroSampleCount: number;
  maxAbsoluteSample: number;
  rms: number;
}

export interface SilentCaptureReacquireInput {
  nativePlatform: boolean;
  speechDetected: boolean;
  elapsedMs: number;
  detectorFrameCount: number;
  zeroSignalFrameCount: number;
  peakRms: number;
  reacquireAttemptCount: number;
  minObservationMs: number;
  minDetectorFrames: number;
  zeroFrameRatio: number;
  maxReacquireAttempts: number;
}

export interface DegenerateCaptureInput {
  nativePlatform: boolean;
  elapsedMs: number;
  detectorFrameCount: number;
  zeroSignalFrameCount: number;
  peakRms: number;
  reacquireAttemptCount: number;
  minObservationMs: number;
  minDetectorFrames: number;
  zeroFrameRatio: number;
  maxWeakPeakRms: number;
  maxReacquireAttempts: number;
}

export interface ExhaustedCaptureRecoveryInput extends CaptureHealthInput {
  reacquireAttemptCount: number;
  maxReacquireAttempts: number;
}

export interface CaptureHealthInput {
  nativePlatform: boolean;
  elapsedMs: number;
  detectorFrameCount: number;
  zeroSignalFrameCount: number;
  peakRms: number;
  minObservationMs: number;
  minDetectorFrames: number;
  zeroFrameRatio: number;
  maxWeakPeakRms: number;
}

export type RecordingValidationResult =
  | { accepted: true }
  | { accepted: false; reason: RecordingRejectionReason };

export type RecordingTerminalPath = "accepted" | "rejected" | "cancelled";
export type PlaybackTerminalPath = "ended" | "interrupted" | "replay-ended";

export interface RecordingRuntimeSnapshot {
  listening: boolean;
  interactionState: "idle" | "listening" | "transcribing";
  recorderAttached: boolean;
  chunksHeld: boolean;
  recordingStarting: boolean;
  speechDetected: boolean;
  turnLocked: boolean;
  requestAbortActive: boolean;
}

export interface PlaybackRuntimeSnapshot {
  audioContextAttached: boolean;
  audioSourceAttached: boolean;
  browserSpeechTimerActive: boolean;
  synthesisMayBeQueued: boolean;
}

export const DEFAULT_VOICE_ACTIVITY_CONFIG: VoiceActivityConfig = {
  speechLevelThreshold: SPEECH_LEVEL_THRESHOLD,
  calibratedSpeechLevelThreshold: 0.006,
  silenceStopMs: SILENCE_STOP_MS,
  minRecordingMs: MIN_RECORDING_MS,
  initialCalibrationMs: 250,
  ambientSmoothing: 0.12,
  speechAmbientMultiplier: 2,
  speechAmbientMargin: 0.006,
  silenceAmbientMultiplier: 1.2,
  silenceAmbientMargin: 0.008,
};

export function initialVoiceActivityState(): VoiceActivityState {
  return {
    ambientLevel: null,
    peakLevel: 0,
    peakElapsedMs: null,
    speechStartThresholdAtPeak: null,
    speechDetected: false,
    silenceStartedAt: null,
    calibrated: false,
  };
}

export function recordingAudioConstraints(
  disableAudioProcessing = false,
): MediaStreamConstraints {
  if (!disableAudioProcessing) return { audio: true };
  return {
    audio: {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
  };
}

export function rawSignalStatsFromByteTimeDomain(
  samples: Uint8Array,
): RawSignalStats {
  let sum = 0;
  let maxAbsoluteSample = 0;
  let nonZeroSampleCount = 0;

  for (const sample of samples) {
    const absoluteSample = Math.abs(sample - 128) / 128;
    if (absoluteSample > 0) nonZeroSampleCount += 1;
    maxAbsoluteSample = Math.max(maxAbsoluteSample, absoluteSample);
    sum += absoluteSample * absoluteSample;
  }

  return {
    nonZeroSampleCount,
    maxAbsoluteSample,
    rms: Math.sqrt(sum / samples.length),
  };
}

export function shouldReacquireSilentNativeCapture({
  nativePlatform,
  speechDetected,
  elapsedMs,
  detectorFrameCount,
  zeroSignalFrameCount,
  peakRms,
  reacquireAttemptCount,
  minObservationMs,
  minDetectorFrames,
  zeroFrameRatio,
  maxReacquireAttempts,
}: SilentCaptureReacquireInput): boolean {
  if (!nativePlatform) return false;
  if (speechDetected) return false;
  if (reacquireAttemptCount >= maxReacquireAttempts) return false;
  if (elapsedMs < minObservationMs) return false;
  if (detectorFrameCount < minDetectorFrames) return false;
  if (detectorFrameCount === 0) return false;

  const observedZeroFrameRatio = zeroSignalFrameCount / detectorFrameCount;
  return observedZeroFrameRatio >= zeroFrameRatio
    && peakRms < DEFAULT_VOICE_ACTIVITY_CONFIG.calibratedSpeechLevelThreshold;
}

export function shouldRejectDegenerateNativeCapture({
  nativePlatform,
  elapsedMs,
  detectorFrameCount,
  zeroSignalFrameCount,
  peakRms,
  reacquireAttemptCount,
  minObservationMs,
  minDetectorFrames,
  zeroFrameRatio,
  maxWeakPeakRms,
  maxReacquireAttempts,
}: DegenerateCaptureInput): boolean {
  if (!isDegenerateNativeCapture({
    nativePlatform,
    elapsedMs,
    detectorFrameCount,
    zeroSignalFrameCount,
    peakRms,
    minObservationMs,
    minDetectorFrames,
    zeroFrameRatio,
    maxWeakPeakRms,
  })) return false;
  return reacquireAttemptCount < maxReacquireAttempts;
}

export function shouldStopExhaustedNativeCaptureRecovery({
  reacquireAttemptCount,
  maxReacquireAttempts,
  ...captureHealth
}: ExhaustedCaptureRecoveryInput): boolean {
  return reacquireAttemptCount >= maxReacquireAttempts
    && isDegenerateNativeCapture(captureHealth);
}

export function isDegenerateNativeCapture({
  nativePlatform,
  elapsedMs,
  detectorFrameCount,
  zeroSignalFrameCount,
  peakRms,
  minObservationMs,
  minDetectorFrames,
  zeroFrameRatio,
  maxWeakPeakRms,
}: CaptureHealthInput): boolean {
  if (!nativePlatform) return false;
  if (elapsedMs < minObservationMs) return false;
  if (detectorFrameCount < minDetectorFrames) return false;
  if (detectorFrameCount === 0) return false;

  const observedZeroFrameRatio = zeroSignalFrameCount / detectorFrameCount;
  return observedZeroFrameRatio >= zeroFrameRatio && peakRms < maxWeakPeakRms;
}

export function validateRecordingForTranscription({
  speechDetected,
  durationMs,
  minRecordingMs,
  chunkCount,
  blobSize,
}: RecordingValidationInput): RecordingValidationResult {
  if (!speechDetected) return { accepted: false, reason: "no-speech-detected" };
  if (durationMs < minRecordingMs) return { accepted: false, reason: "too-short" };
  if (chunkCount === 0) return { accepted: false, reason: "no-recorder-chunks" };
  if (blobSize === 0) return { accepted: false, reason: "zero-byte-blob" };
  return { accepted: true };
}

export function terminalRecordingRuntimeState(
  path: RecordingTerminalPath,
): RecordingRuntimeSnapshot {
  const accepted = path === "accepted";
  return {
    listening: false,
    interactionState: accepted ? "transcribing" : "idle",
    recorderAttached: false,
    chunksHeld: false,
    recordingStarting: false,
    speechDetected: false,
    turnLocked: accepted,
    requestAbortActive: accepted,
  };
}

export function terminalPlaybackRuntimeState(
  path: PlaybackTerminalPath,
): PlaybackRuntimeSnapshot {
  void path;
  return {
    audioContextAttached: false,
    audioSourceAttached: false,
    browserSpeechTimerActive: false,
    synthesisMayBeQueued: false,
  };
}

export function inspectVoiceActivityLevel(
  state: VoiceActivityState,
  level: number,
  now: number,
  recordingStartedAt: number,
  config = DEFAULT_VOICE_ACTIVITY_CONFIG,
): VoiceActivityInspection {
  const ambientLevel = state.ambientLevel ?? level;
  const elapsedMs = now - recordingStartedAt;
  const calibrated = state.calibrated || elapsedMs >= config.initialCalibrationMs;

  return {
    ambientLevel,
    calibrated,
    level,
    silenceThreshold: Math.max(
      config.speechLevelThreshold,
      ambientLevel * config.silenceAmbientMultiplier,
      ambientLevel + config.silenceAmbientMargin,
    ),
    speechDetected: state.speechDetected,
    speechStartThreshold: Math.max(
      config.calibratedSpeechLevelThreshold,
      ambientLevel * config.speechAmbientMultiplier,
      ambientLevel + config.speechAmbientMargin,
    ),
  };
}

export function evaluateVoiceActivityLevel(
  state: VoiceActivityState,
  level: number,
  now: number,
  recordingStartedAt: number,
  config = DEFAULT_VOICE_ACTIVITY_CONFIG,
): VoiceActivityDecision {
  const ambientLevel = state.ambientLevel ?? level;
  const elapsedMs = now - recordingStartedAt;
  const calibrated = state.calibrated || elapsedMs >= config.initialCalibrationMs;
  const speechStartThreshold = Math.max(
    config.calibratedSpeechLevelThreshold,
    ambientLevel * config.speechAmbientMultiplier,
    ambientLevel + config.speechAmbientMargin,
  );
  const silenceThreshold = Math.max(
    config.speechLevelThreshold,
    ambientLevel * config.silenceAmbientMultiplier,
    ambientLevel + config.silenceAmbientMargin,
  );

  state.ambientLevel = ambientLevel;
  if (level > state.peakLevel) {
    state.peakLevel = level;
    state.peakElapsedMs = elapsedMs;
    state.speechStartThresholdAtPeak = speechStartThreshold;
  }
  state.calibrated = calibrated;

  if (!state.speechDetected) {
    if (level >= speechStartThreshold) {
      state.speechDetected = true;
      state.silenceStartedAt = null;
    } else {
      state.ambientLevel = ambientLevel + (level - ambientLevel) * config.ambientSmoothing;
    }
  } else if (level <= silenceThreshold && now - recordingStartedAt >= config.minRecordingMs) {
    state.silenceStartedAt ??= now;
  } else {
    state.silenceStartedAt = null;
  }

  return {
    ambientLevel: state.ambientLevel,
    level,
    silenceStartedAt: state.silenceStartedAt,
    silenceThreshold,
    speechDetected: state.speechDetected,
    speechStartThreshold,
    shouldStop: state.silenceStartedAt !== null && now - state.silenceStartedAt >= config.silenceStopMs,
  };
}
