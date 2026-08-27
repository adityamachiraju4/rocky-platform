"""Deterministic language stabilization for local speech transcripts."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.conversation.language import SUPPORTED_RESPONSE_LANGUAGES, normalize_language

LOW_LANGUAGE_CONFIDENCE = 0.65
HIGH_LANGUAGE_CONFIDENCE = 0.80

_STRONG_SCRIPT_MIN_CHARS = 2

_SCRIPT_RANGES: tuple[tuple[str, str, range], ...] = (
    ("hi", "devanagari", range(0x0900, 0x0980)),
    ("bn", "bengali", range(0x0980, 0x0A00)),
    ("ta", "tamil", range(0x0B80, 0x0C00)),
    ("te", "telugu", range(0x0C00, 0x0C80)),
    ("ja", "japanese", range(0x3040, 0x3100)),
)

_ENGLISH_WORDS = re.compile(
    r"\b(?:what|which|who|when|where|why|how|do|does|did|is|are|am|have|"
    r"has|had|can|could|would|should|the|a|an|my|me|i|today|tomorrow|"
    r"task|tasks|project|projects|remind|reminder)\b",
    re.I,
)
_SPANISH_EVIDENCE = re.compile(
    r"[¿¡áéíóúüñ]|\b(?:qué|que|cu[aá]les|tareas|tengo|hoy|mañana|"
    r"recu[eé]rdame|proyecto|proyectos)\b",
    re.I,
)
_FRENCH_EVIDENCE = re.compile(
    r"[àâçéèêëîïôûùüÿœ]|\b(?:quelles?|t[aâ]ches|aujourd'hui|"
    r"demain|rappelle-moi|projets?)\b",
    re.I,
)
_LATIN_LETTER = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")


@dataclass(frozen=True)
class ScriptEvidence:
    language: str | None
    script: str | None
    counts: dict[str, int]


def stabilize_transcription_language(
    transcript: str,
    detected_language: str | None,
    language_probability: float | None,
    *,
    previous_language: str | None = None,
) -> str | None:
    """Choose the response language Rocky should trust for this transcript.

    The decision is intentionally local and conservative: strong script evidence
    wins, high-confidence supported Whisper labels are trusted when not
    contradicted, and the bounded prior is only a tie-breaker for weak ambiguous
    detections.
    """

    detected = normalize_language(detected_language)
    previous = normalize_language(previous_language)
    probability = _normalized_probability(language_probability)
    script = script_evidence(transcript)

    if script.language in SUPPORTED_RESPONSE_LANGUAGES:
        return script.language

    latin = _has_latin_text(transcript)
    if latin:
        latin_language = _latin_language(transcript)
        if latin_language is not None:
            return latin_language

    if (
        detected in SUPPORTED_RESPONSE_LANGUAGES
        and probability is not None
        and probability >= HIGH_LANGUAGE_CONFIDENCE
    ):
        return detected

    if (
        previous in SUPPORTED_RESPONSE_LANGUAGES
        and (detected is None or probability is None or probability < LOW_LANGUAGE_CONFIDENCE)
        and not script.language
        and _is_ambiguous_for_previous(transcript)
    ):
        return previous

    if (
        detected in SUPPORTED_RESPONSE_LANGUAGES
        and (probability is None or probability >= LOW_LANGUAGE_CONFIDENCE)
    ):
        return detected

    return None


def script_evidence(transcript: str) -> ScriptEvidence:
    counts = {
        language: sum(1 for character in transcript if ord(character) in block)
        for language, _script, block in _SCRIPT_RANGES
    }
    strongest = max(counts.items(), key=lambda item: item[1], default=(None, 0))
    if strongest[0] is None or strongest[1] < _STRONG_SCRIPT_MIN_CHARS:
        return ScriptEvidence(language=None, script=None, counts=counts)
    script_name = next(
        script for language, script, _block in _SCRIPT_RANGES if language == strongest[0]
    )
    return ScriptEvidence(language=strongest[0], script=script_name, counts=counts)


def _normalized_probability(value: float | None) -> float | None:
    if not isinstance(value, (float, int)):
        return None
    return max(0.0, min(1.0, float(value)))


def _has_latin_text(transcript: str) -> bool:
    return bool(_LATIN_LETTER.search(transcript))


def _latin_language(transcript: str) -> str | None:
    if _SPANISH_EVIDENCE.search(transcript):
        return "es"
    if _FRENCH_EVIDENCE.search(transcript):
        return "fr"
    if _ENGLISH_WORDS.search(transcript) and not re.search(
        r"[¿¡áéíóúüñàâçèêëîïôûùüÿœ]", transcript, re.I
    ):
        return "en"
    return None


def _is_ambiguous_for_previous(transcript: str) -> bool:
    return not any(
        (
            _SPANISH_EVIDENCE.search(transcript),
            _FRENCH_EVIDENCE.search(transcript),
            _ENGLISH_WORDS.search(transcript),
        )
    )
