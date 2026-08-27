# Multilingual Voice Hardening

These defects are known and intentionally deferred from M6.5. Do not treat
them as fixed until they pass physical-device QA across browser and installed
PWA modes.

## DEFERRED-MULTI-001: Automatic Language Detection

Automatic STT language detection remains imperfect in real-device cases.

Observed examples:
- Telugu may be classified as Hindi or Bengali.
- English may occasionally be classified as another language.

A deterministic backend stabilizer now considers transcript text, raw Whisper
language, probability, and script evidence before choosing Rocky's final
conversation language. That reduces the known failure mode but does not replace
physical retesting.

Retest phrases:
- English: "What tasks do I have today?"
- English: "Can you hear me?"
- Telugu: "నా పనులు ఏమిటి?"
- Telugu: "డాకర్ అంటే ఏమిటి?"
- Hindi: "मेरे काम क्या हैं?"
- Tamil: "எனக்கு என்ன வேலைகள் உள்ளன?"
- Spanish: "¿Qué tareas tengo hoy?"

## DEFERRED-MULTI-002: Intermittent Non-English Turns

Some multilingual turns intermittently fail to produce a response. Keep this
separate from TTS; first determine whether the failure happens during STT,
conversation routing, provider understanding, or response rendering.

## DEFERRED-MULTI-003: Browser/Device Speech Reliability

Non-English automatic browser/device speech can be silent on some real devices.
Replay usually works. Preserve Replay and Spoken Replies controls while this is
investigated.

Known symptoms:
- automatic non-English speech may not start
- Replay may start speech successfully
- behavior varies by browser, installed PWA mode, and user activation state

## Next Investigation

Use the M4 real-device QA runbook and record device model, OS/browser version,
browser vs installed mode, exact language, transcript, final response language,
speech provider/fallback, and whether Replay changed the result. Do not record
passwords, tokens, private tasks, or raw audio.
