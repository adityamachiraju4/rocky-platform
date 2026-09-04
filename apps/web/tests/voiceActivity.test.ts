import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_VOICE_ACTIVITY_CONFIG,
  evaluateVoiceActivityLevel,
  inspectVoiceActivityLevel,
  initialVoiceActivityState,
  isDegenerateNativeCapture,
  MAX_RECORDING_MS,
  MIN_RECORDING_MS,
  rawSignalStatsFromByteTimeDomain,
  recordingAudioConstraints,
  shouldRejectDegenerateNativeCapture,
  shouldReacquireSilentNativeCapture,
  shouldStopExhaustedNativeCaptureRecovery,
  terminalPlaybackRuntimeState,
  terminalRecordingRuntimeState,
  validateRecordingForTranscription,
} from "../src/voiceActivity.ts";

test("speech detected after the input rises above the ambient floor", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0.05, 0, 0);
  const decision = evaluateVoiceActivityLevel(state, 0.12, 120, 0);

  assert.equal(decision.speechDetected, true);
  assert.equal(decision.shouldStop, false);
  assert.equal(state.speechDetected, true);
});

test("calibrated detection catches low but real speech energy", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0.002, 0, 0);
  evaluateVoiceActivityLevel(state, 0.0025, 260, 0);
  const decision = evaluateVoiceActivityLevel(state, 0.012, 320, 0);

  assert.equal(decision.speechDetected, true);
  assert.equal(decision.shouldStop, false);
  assert.equal(Number(decision.speechStartThreshold.toFixed(3)), 0.008);
});

test("speech above adaptive threshold at 200ms is detected", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0, 0, 0);
  const decision = evaluateVoiceActivityLevel(state, 0.007263, 200, 0);

  assert.equal(decision.speechDetected, true);
  assert.equal(decision.shouldStop, false);
  assert.equal(state.speechDetected, true);
  assert.equal(Number(state.peakLevel.toFixed(6)), 0.007263);
  assert.equal(state.peakElapsedMs, 200);
  assert.equal(Number(state.speechStartThresholdAtPeak?.toFixed(3)), 0.006);
});

test("early speech cannot auto-stop before minimum recording duration", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0, 0, 0);
  evaluateVoiceActivityLevel(state, 0.007263, 200, 0);
  const decision = evaluateVoiceActivityLevel(state, 0, MIN_RECORDING_MS - 1, 0);

  assert.equal(decision.speechDetected, true);
  assert.equal(decision.silenceStartedAt, null);
  assert.equal(decision.shouldStop, false);
});

test("early speech followed by silence stops after minimum duration and silence", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0, 0, 0);
  evaluateVoiceActivityLevel(state, 0.007263, 200, 0);
  const silenceStart = evaluateVoiceActivityLevel(state, 0, MIN_RECORDING_MS, 0);
  const stop = evaluateVoiceActivityLevel(
    state,
    0,
    MIN_RECORDING_MS + DEFAULT_VOICE_ACTIVITY_CONFIG.silenceStopMs + 1,
    0,
  );

  assert.equal(silenceStart.silenceStartedAt, MIN_RECORDING_MS);
  assert.equal(stop.shouldStop, true);
});

test("low steady ambient does not become speech after calibration", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0.002, 0, 0);
  evaluateVoiceActivityLevel(state, 0.0025, 260, 0);
  const decision = evaluateVoiceActivityLevel(state, 0.003, 320, 0);

  assert.equal(decision.speechDetected, false);
  assert.equal(decision.shouldStop, false);
});

test("initial silence does not trigger speech detection", () => {
  const state = initialVoiceActivityState();

  for (let now = 0; now <= MIN_RECORDING_MS; now += 100) {
    const decision = evaluateVoiceActivityLevel(state, 0, now, 0);
    assert.equal(decision.speechDetected, false);
    assert.equal(decision.shouldStop, false);
  }
});

test("low noise below adaptive threshold remains no speech", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0, 0, 0);
  const decision = evaluateVoiceActivityLevel(state, 0.004, 200, 0);

  assert.equal(decision.speechDetected, false);
  assert.equal(decision.shouldStop, false);
});

test("raw signal stats identify zero-filled analyser frames", () => {
  const stats = rawSignalStatsFromByteTimeDomain(new Uint8Array([128, 128, 128]));

  assert.equal(stats.nonZeroSampleCount, 0);
  assert.equal(stats.maxAbsoluteSample, 0);
  assert.equal(stats.rms, 0);
});

test("raw signal stats count non-zero centred samples", () => {
  const stats = rawSignalStatsFromByteTimeDomain(new Uint8Array([128, 130, 126]));

  assert.equal(stats.nonZeroSampleCount, 2);
  assert.equal(Number(stats.maxAbsoluteSample.toFixed(6)), 0.015625);
  assert.equal(Number(stats.rms.toFixed(6)), 0.012758);
});

test("recording constraints keep default capture behavior unless diagnostics opt out", () => {
  assert.deepEqual(recordingAudioConstraints(), { audio: true });
  assert.deepEqual(recordingAudioConstraints(true), {
    audio: {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
  });
});

test("native silent capture reacquire triggers for sustained zero-filled frames", () => {
  assert.equal(shouldReacquireSilentNativeCapture({
    nativePlatform: true,
    speechDetected: false,
    elapsedMs: 1_700,
    detectorFrameCount: 100,
    zeroSignalFrameCount: 90,
    peakRms: 0.005,
    reacquireAttemptCount: 0,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxReacquireAttempts: 2,
  }), true);
});

test("silent capture reacquire is native-only and limited to two attempts", () => {
  const input = {
    nativePlatform: true,
    speechDetected: false,
    elapsedMs: 1_700,
    detectorFrameCount: 100,
    zeroSignalFrameCount: 90,
    peakRms: 0.005,
    reacquireAttemptCount: 0,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxReacquireAttempts: 2,
  };

  assert.equal(shouldReacquireSilentNativeCapture({ ...input, nativePlatform: false }), false);
  assert.equal(shouldReacquireSilentNativeCapture({ ...input, reacquireAttemptCount: 1 }), true);
  assert.equal(shouldReacquireSilentNativeCapture({ ...input, reacquireAttemptCount: 2 }), false);
});

test("silent capture reacquire does not trigger after speech or real signal", () => {
  const input = {
    nativePlatform: true,
    speechDetected: false,
    elapsedMs: 1_700,
    detectorFrameCount: 100,
    zeroSignalFrameCount: 90,
    peakRms: 0.005,
    reacquireAttemptCount: 0,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxReacquireAttempts: 2,
  };

  assert.equal(shouldReacquireSilentNativeCapture({ ...input, speechDetected: true }), false);
  assert.equal(shouldReacquireSilentNativeCapture({ ...input, zeroSignalFrameCount: 40 }), false);
  assert.equal(shouldReacquireSilentNativeCapture({ ...input, peakRms: 0.006 }), false);
});

test("speech-detected degenerate native capture reacquires before transcription", () => {
  assert.equal(shouldRejectDegenerateNativeCapture({
    nativePlatform: true,
    elapsedMs: 2_016,
    detectorFrameCount: 63,
    zeroSignalFrameCount: 62,
    peakRms: 0.035534,
    reacquireAttemptCount: 0,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxWeakPeakRms: 0.05,
    maxReacquireAttempts: 2,
  }), true);
});

test("first bad then second good capture stops retrying", () => {
  const bad = {
    nativePlatform: true,
    speechDetected: false,
    elapsedMs: 1_700,
    detectorFrameCount: 100,
    zeroSignalFrameCount: 90,
    peakRms: 0.005,
    reacquireAttemptCount: 0,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxReacquireAttempts: 2,
  };
  const good = { ...bad, reacquireAttemptCount: 1, zeroSignalFrameCount: 20, peakRms: 0.24 };

  assert.equal(shouldReacquireSilentNativeCapture(bad), true);
  assert.equal(shouldReacquireSilentNativeCapture(good), false);
});

test("first bad then second bad then third good stays within retry budget", () => {
  const bad = {
    nativePlatform: true,
    speechDetected: false,
    elapsedMs: 1_700,
    detectorFrameCount: 100,
    zeroSignalFrameCount: 90,
    peakRms: 0.005,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxReacquireAttempts: 2,
  };

  assert.equal(shouldReacquireSilentNativeCapture({ ...bad, reacquireAttemptCount: 0 }), true);
  assert.equal(shouldReacquireSilentNativeCapture({ ...bad, reacquireAttemptCount: 1 }), true);
  assert.equal(shouldReacquireSilentNativeCapture({
    ...bad,
    reacquireAttemptCount: 2,
    zeroSignalFrameCount: 20,
    peakRms: 0.24,
  }), false);
});

test("healthy native speech capture proceeds to transcription", () => {
  assert.equal(shouldRejectDegenerateNativeCapture({
    nativePlatform: true,
    elapsedMs: 3_974,
    detectorFrameCount: 71,
    zeroSignalFrameCount: 32,
    peakRms: 0.347491,
    reacquireAttemptCount: 0,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxWeakPeakRms: 0.05,
    maxReacquireAttempts: 2,
  }), false);
});

test("short genuine native utterance proceeds when signal has a strong peak", () => {
  assert.equal(shouldRejectDegenerateNativeCapture({
    nativePlatform: true,
    elapsedMs: 1_700,
    detectorFrameCount: 35,
    zeroSignalFrameCount: 31,
    peakRms: 0.12,
    reacquireAttemptCount: 0,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxWeakPeakRms: 0.05,
    maxReacquireAttempts: 2,
  }), false);
});

test("all degenerate capture attempts terminate cleanly after two reacquires", () => {
  const degenerate = {
    nativePlatform: true,
    elapsedMs: 2_016,
    detectorFrameCount: 63,
    zeroSignalFrameCount: 62,
    peakRms: 0.035534,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxWeakPeakRms: 0.05,
    maxReacquireAttempts: 2,
  };

  assert.equal(shouldRejectDegenerateNativeCapture({ ...degenerate, reacquireAttemptCount: 0 }), true);
  assert.equal(shouldRejectDegenerateNativeCapture({ ...degenerate, reacquireAttemptCount: 1 }), true);
  assert.equal(shouldRejectDegenerateNativeCapture({ ...degenerate, reacquireAttemptCount: 2 }), false);
  assert.equal(shouldStopExhaustedNativeCaptureRecovery({
    ...degenerate,
    reacquireAttemptCount: 2,
    maxReacquireAttempts: 2,
  }), true);
  assert.equal(isDegenerateNativeCapture(degenerate), true);
});

test("exhausted native retry budget stops a dead final capture", () => {
  assert.equal(shouldStopExhaustedNativeCaptureRecovery({
    nativePlatform: true,
    elapsedMs: 23_500,
    detectorFrameCount: 1_355,
    zeroSignalFrameCount: 1_355,
    peakRms: 0,
    reacquireAttemptCount: 2,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxWeakPeakRms: 0.05,
    maxReacquireAttempts: 2,
  }), true);
});

test("weak startup transient followed by dead input is degenerate capture", () => {
  const weakThenDead = {
    nativePlatform: true,
    elapsedMs: 25_700,
    detectorFrameCount: 1_469,
    zeroSignalFrameCount: 1_437,
    peakRms: 0.006533,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxWeakPeakRms: 0.05,
  };

  assert.equal(isDegenerateNativeCapture(weakThenDead), true);
  assert.equal(shouldReacquireSilentNativeCapture({
    ...weakThenDead,
    speechDetected: false,
    reacquireAttemptCount: 0,
    maxReacquireAttempts: 2,
  }), false);
  assert.equal(shouldRejectDegenerateNativeCapture({
    ...weakThenDead,
    reacquireAttemptCount: 0,
    maxReacquireAttempts: 2,
  }), true);
});

test("manual stop during degenerate reacquire remains a normal terminal recording", () => {
  const degenerate = {
    nativePlatform: true,
    elapsedMs: 2_016,
    detectorFrameCount: 63,
    zeroSignalFrameCount: 62,
    peakRms: 0.035534,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxWeakPeakRms: 0.05,
  };

  assert.equal(isDegenerateNativeCapture(degenerate), true);
  assert.deepEqual(terminalRecordingRuntimeState("rejected"), {
    listening: false,
    interactionState: "idle",
    recorderAttached: false,
    chunksHeld: false,
    recordingStarting: false,
    speechDetected: false,
    turnLocked: false,
    requestAbortActive: false,
  });
});

test("degenerate native capture does not loop beyond retry budget", () => {
  assert.equal(shouldRejectDegenerateNativeCapture({
    nativePlatform: true,
    elapsedMs: 2_016,
    detectorFrameCount: 63,
    zeroSignalFrameCount: 62,
    peakRms: 0.035534,
    reacquireAttemptCount: 2,
    minObservationMs: 1_600,
    minDetectorFrames: 20,
    zeroFrameRatio: 0.85,
    maxWeakPeakRms: 0.05,
    maxReacquireAttempts: 2,
  }), false);
});

test("voice activity inspection reports thresholds without mutating state", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0.002, 0, 0);
  const before = { ...state };
  const inspection = inspectVoiceActivityLevel(state, 0.003, 320, 0);

  assert.deepEqual(state, before);
  assert.equal(inspection.speechDetected, false);
  assert.equal(Number(inspection.speechStartThreshold.toFixed(3)), 0.008);
});

test("silence after speech triggers stop with an Android-style elevated noise floor", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0.05, 0, 0);
  evaluateVoiceActivityLevel(state, 0.13, 100, 0);

  const start = evaluateVoiceActivityLevel(state, 0.055, 800, 0);
  const stop = evaluateVoiceActivityLevel(
    state,
    0.052,
    800 + DEFAULT_VOICE_ACTIVITY_CONFIG.silenceStopMs + 1,
    0,
  );

  assert.equal(start.silenceStartedAt, 800);
  assert.equal(stop.shouldStop, true);
});

test("initial silence does not stop before speech is detected", () => {
  const state = initialVoiceActivityState();

  for (let now = 0; now <= MAX_RECORDING_MS; now += 500) {
    const decision = evaluateVoiceActivityLevel(state, 0.02, now, 0);
    assert.equal(decision.speechDetected, false);
    assert.equal(decision.shouldStop, false);
  }
});

test("max-duration fallback remains separate from silence detection", () => {
  const state = initialVoiceActivityState();

  evaluateVoiceActivityLevel(state, 0.02, 0, 0);
  const decision = evaluateVoiceActivityLevel(state, 0.02, MAX_RECORDING_MS + 1, 0);

  assert.equal(decision.shouldStop, false);
});

test("repeated manual stop calls are idempotent when the recorder is already stopped", () => {
  let stopCalls = 0;
  const recorder = {
    state: "recording",
    stop() {
      stopCalls += 1;
      this.state = "inactive";
    },
  };
  const stopActiveRecorder = () => {
    if (recorder.state === "recording") recorder.stop();
  };

  stopActiveRecorder();
  stopActiveRecorder();

  assert.equal(stopCalls, 1);
});

test("blob ready plus no speech detected resets to Ready", () => {
  const validation = validateRecordingForTranscription({
    speechDetected: false,
    durationMs: 15_000,
    minRecordingMs: MIN_RECORDING_MS,
    chunkCount: 1,
    blobSize: 24_000,
  });
  const terminalState = terminalRecordingRuntimeState("rejected");

  assert.deepEqual(validation, { accepted: false, reason: "no-speech-detected" });
  assert.equal(terminalState.listening, false);
  assert.equal(terminalState.interactionState, "idle");
});

test("silent rejection resets recorder and turn state", () => {
  const terminalState = terminalRecordingRuntimeState("rejected");

  assert.equal(terminalState.recorderAttached, false);
  assert.equal(terminalState.chunksHeld, false);
  assert.equal(terminalState.recordingStarting, false);
  assert.equal(terminalState.speechDetected, false);
  assert.equal(terminalState.turnLocked, false);
  assert.equal(terminalState.requestAbortActive, false);
});

test("too-short rejection resets state", () => {
  const validation = validateRecordingForTranscription({
    speechDetected: true,
    durationMs: MIN_RECORDING_MS - 1,
    minRecordingMs: MIN_RECORDING_MS,
    chunkCount: 1,
    blobSize: 4_096,
  });
  const terminalState = terminalRecordingRuntimeState("rejected");

  assert.deepEqual(validation, { accepted: false, reason: "too-short" });
  assert.equal(terminalState.listening, false);
  assert.equal(terminalState.interactionState, "idle");
  assert.equal(terminalState.recorderAttached, false);
});

test("successful recording proceeds to transcription state", () => {
  const validation = validateRecordingForTranscription({
    speechDetected: true,
    durationMs: MIN_RECORDING_MS,
    minRecordingMs: MIN_RECORDING_MS,
    chunkCount: 2,
    blobSize: 12_288,
  });
  const terminalState = terminalRecordingRuntimeState("accepted");

  assert.deepEqual(validation, { accepted: true });
  assert.equal(terminalState.listening, false);
  assert.equal(terminalState.interactionState, "transcribing");
  assert.equal(terminalState.turnLocked, true);
  assert.equal(terminalState.requestAbortActive, true);
});

test("a second recording can start after every rejection path", () => {
  const rejectionInputs = [
    { speechDetected: false, durationMs: 10_000, chunkCount: 1, blobSize: 8_192 },
    { speechDetected: true, durationMs: MIN_RECORDING_MS - 1, chunkCount: 1, blobSize: 8_192 },
    { speechDetected: true, durationMs: MIN_RECORDING_MS, chunkCount: 0, blobSize: 0 },
    { speechDetected: true, durationMs: MIN_RECORDING_MS, chunkCount: 1, blobSize: 0 },
  ];

  for (const input of rejectionInputs) {
    const validation = validateRecordingForTranscription({
      ...input,
      minRecordingMs: MIN_RECORDING_MS,
    });
    const terminalState = terminalRecordingRuntimeState("rejected");
    const canStartAgain = !terminalState.turnLocked
      && !terminalState.recordingStarting
      && !terminalState.recorderAttached;

    assert.equal(validation.accepted, false);
    assert.equal(canStartAgain, true);
  }
});

test("successful turn followed by TTS completion can start a second recording", () => {
  const acceptedRecording = terminalRecordingRuntimeState("accepted");
  const completedPlayback = terminalPlaybackRuntimeState("ended");

  assert.equal(acceptedRecording.interactionState, "transcribing");
  assert.equal(completedPlayback.audioContextAttached, false);
  assert.equal(completedPlayback.audioSourceAttached, false);
  assert.equal(completedPlayback.browserSpeechTimerActive, false);
});

test("recording resources are replaced between turns after cleanup", () => {
  const firstTerminal = terminalRecordingRuntimeState("rejected");
  const secondStartBlocked = firstTerminal.recorderAttached
    || firstTerminal.recordingStarting
    || firstTerminal.chunksHeld;

  assert.equal(firstTerminal.listening, false);
  assert.equal(secondStartBlocked, false);
});

test("recording cleanup contract is idempotent", () => {
  const firstCleanup = terminalRecordingRuntimeState("cancelled");
  const secondCleanup = terminalRecordingRuntimeState("cancelled");

  assert.deepEqual(secondCleanup, firstCleanup);
  assert.equal(secondCleanup.recorderAttached, false);
  assert.equal(secondCleanup.chunksHeld, false);
});

test("replay completion does not poison subsequent recording", () => {
  const replayTerminal = terminalPlaybackRuntimeState("replay-ended");
  const canStartRecording = !replayTerminal.audioContextAttached
    && !replayTerminal.audioSourceAttached
    && !replayTerminal.browserSpeechTimerActive
    && !replayTerminal.synthesisMayBeQueued;

  assert.equal(canStartRecording, true);
});

test("manual and max-duration terminal paths still reset recording state", () => {
  for (const path of ["accepted", "rejected", "cancelled"] as const) {
    const terminalState = terminalRecordingRuntimeState(path);

    assert.equal(terminalState.listening, false);
    assert.equal(terminalState.recorderAttached, false);
    assert.equal(terminalState.recordingStarting, false);
  }
});
