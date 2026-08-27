"""Small, local language helpers for conversation routing and templates."""
from __future__ import annotations

import re

SUPPORTED_RESPONSE_LANGUAGES = frozenset({"en", "hi", "te", "ta", "es", "fr", "ja"})

_EXPLICIT_LANGUAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("en", re.compile(r"\b(?:in English|English mein|English lo)\b", re.I)),
    ("hi", re.compile(r"हिंदी में|\bHindi mein\b", re.I)),
    ("te", re.compile(r"తెలుగులో|\bTelugu lo\b", re.I)),
    ("ta", re.compile(r"தமிழில்|\bTamil(?:il| la)?\b", re.I)),
    ("es", re.compile(r"\ben español\b", re.I)),
    ("fr", re.compile(r"\ben français\b", re.I)),
    ("ja", re.compile(r"日本語で")),
)

_TASK_QUERY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:काम|कार्य).*(?:क्या|कौन)|(?:मेरे|मुझे).*(?:काम|कार्य)"),
    re.compile(
        r"(?:నా|నాకు).*(?:పనులు|tasks)|"
        r"(?:పనులు|tasks).*(?:ఏంటి|ఏమిటి|ఉన్నాయి)",
        re.I,
    ),
    re.compile(
        r"(?:என்|எனது|எனக்கு|என்ன).*(?:வேலைகள்|பணிகள்)|"
        r"(?:வேலைகள்|பணிகள்).*(?:என்ன|எவை)"
    ),
    re.compile(r"\b(?:qué|que|cuáles|cuales)\s+tareas\b|\btareas\s+tengo\b", re.I),
    re.compile(r"\bquelles?\s+tâches\b|\btâches\s+(?:ai-je|j'ai)\b", re.I),
    re.compile(r"(?:今日の)?タスク|タスクは何"),
    re.compile(r"\b(?:naa|naaku)\s+tasks?\b.*\benti\b", re.I),
)

_LIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("weather", re.compile(r"(?:आज.*मौसम|मौसम.*आज)")),
    ("weather", re.compile(r"(?:వాతావరణం|weather).*(?:ఎలా|ఈరోజు|హైదరాబాద్)", re.I)),
    ("weather", re.compile(r"(?:இன்றைய.*வானிலை|வானிலை.*இன்று)")),
    ("weather", re.compile(r"\b(?:tiempo|clima)\b.*\bhoy\b", re.I)),
    ("weather", re.compile(r"\b(?:météo|temps)\b.*\baujourd'hui\b", re.I)),
    ("weather", re.compile(r"(?:今日の天気|天気.*今日)")),
)

_GENERAL_FOLLOW_UP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:tell me more|explain more|simpler|more simply|what about that|"
        r"why is that|how so|why would I use (?:it|that)|"
        r"what (?:is|does) (?:it|that))\b",
        re.I,
    ),
    re.compile(r"(?:और|इसे).*(?:सरल|आसान|बताओ|समझाओ)"),
    re.compile(r"(?:ఇంకా|దాన్ని).*(?:సింపుల్|సులభం|చెప్పు|వివరించు)"),
    re.compile(r"(?:இன்னும்|அதை).*(?:எளிமையாக|விளக்கு|சொல்லு)"),
    re.compile(r"\b(?:más simple|explícalo más|cuéntame más)\b", re.I),
    re.compile(r"\b(?:plus simple|explique davantage|dis-m'en plus)\b", re.I),
    re.compile(r"(?:もっと簡単|詳しく|もう少し説明)"),
)

_LIVE_REPLIES = {
    "en": "I don't have live access to {category} yet, so I can't give you a reliable current answer.",
    "hi": "मेरे पास अभी लाइव {category} की जानकारी नहीं है, इसलिए मैं विश्वसनीय वर्तमान उत्तर नहीं दे सकता।",
    "te": "నాకు ఇంకా ప్రత్యక్ష {category} సమాచారం అందుబాటులో లేదు, కాబట్టి నమ్మదగిన ప్రస్తుత సమాధానం ఇవ్వలేను.",
    "ta": "என்னிடம் இன்னும் நேரடி {category} தகவல் இல்லை, அதனால் நம்பகமான தற்போதைய பதிலை வழங்க முடியாது.",
    "es": "Todavía no tengo acceso a datos de {category} en tiempo real, así que no puedo darte una respuesta actual fiable.",
    "fr": "Je n'ai pas encore accès aux données de {category} en direct, donc je ne peux pas donner de réponse actuelle fiable.",
    "ja": "まだ{category}のライブ情報にはアクセスできないため、信頼できる現在の回答はできません。",
}

_CATEGORY_NAMES = {
    "weather": {
        "hi": "मौसम",
        "te": "వాతావరణ",
        "ta": "வானிலை",
        "es": "clima",
        "fr": "météo",
        "ja": "天気",
    }
}


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower().replace("_", "-").split("-", 1)[0]
    return normalized if re.fullmatch(r"[a-z]{2,3}", normalized) else None


def response_language(message: str, supplied_language: str | None = None) -> str:
    for language, pattern in _EXPLICIT_LANGUAGE_PATTERNS:
        if pattern.search(message):
            return language

    supplied = normalize_language(supplied_language)
    if supplied:
        return supplied
    if re.search(r"[\u0900-\u097f]", message):
        return "hi"
    if re.search(r"[\u0c00-\u0c7f]", message):
        return "te"
    if re.search(r"[\u0b80-\u0bff]", message):
        return "ta"
    if re.search(r"[\u3040-\u30ff]", message):
        return "ja"
    if re.search(r"[¿¡]|\b(?:qué|cuáles|tareas|tengo|hoy)\b", message, re.I):
        return "es"
    if re.search(r"\b(?:quelles|tâches|aujourd'hui|qu'est-ce)\b", message, re.I):
        return "fr"
    if re.search(r"\b(?:naa|naaku|enti|cheyyi|cheppu|repu)\b", message, re.I):
        return "te"
    if re.search(r"\b(?:hindi mein|yaad dilana)\b", message, re.I):
        return "hi"
    return "en"


def normalize_capability_message(message: str) -> str:
    if any(pattern.search(message) for pattern in _TASK_QUERY_PATTERNS):
        return "What tasks do I have?"
    return message


def live_information_category(message: str) -> str | None:
    for category, pattern in _LIVE_PATTERNS:
        if pattern.search(message):
            return category
    return None


def is_general_follow_up(message: str) -> bool:
    return any(pattern.search(message) for pattern in _GENERAL_FOLLOW_UP_PATTERNS)


def live_information_reply(category: str, language: str) -> str:
    template = _LIVE_REPLIES.get(language, _LIVE_REPLIES["en"])
    localized_category = _CATEGORY_NAMES.get(category, {}).get(language, category)
    return template.format(category=localized_category)
