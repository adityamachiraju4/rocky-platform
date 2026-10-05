"""Conservative current-turn authorization for Phase A memory mutations.

English explicit imperative forms are recognized. Other wording must be
clarified, never inferred from stored/retrieved text or prior model output.
"""
import re

_PREFIX = r"(?:^|[.,;!]\s*|\b(?:and|then|also)\s+)(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
_REMEMBER = re.compile(_PREFIX + r"(?:remember\s+(?!not\b|nothing\b|whether\b|when\b|what\b)|(?:save|store)\s+(?:this\s+)?(?:as\s+)?(?:a\s+)?(?:preference|fact|personal\s+memory|memory)\b)", re.I)
_UPDATE = re.compile(_PREFIX + r"(?:update|change|correct|revise)\s+", re.I)
_FORGET = re.compile(_PREFIX + r"forget\s+", re.I)


def explicit_memory_operation(message: str, action: str) -> bool:
    if action == "memory.list":
        return bool(re.search(_PREFIX + r"(?:list|show)\s+.*\bmemor(?:y|ies)\b", message, re.I)
                    or re.match(r"^\s*what\s+(?:do you remember about me|are my memories)", message, re.I))
    if not re.match(r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
                    r"(?:remember|save|store|update|change|correct|revise|forget|create|make|add|remind|show|list)\b", message, re.I):
        return False
    if action == "memory.remember":
        return _REMEMBER.search(message) is not None
    if action == "memory.update":
        return _UPDATE.search(message) is not None
    if action == "memory.forget":
        return _FORGET.search(message) is not None
    return True


def remember_text(message: str) -> str | None:
    """Only simple full-turn imperative forms receive deterministic parsing."""
    if not explicit_memory_operation(message, "memory.remember"):
        return None
    if re.search(r"\b(?:and|then|also)\s+(?:create|make|add|update|forget|remind|show|list)\b", message, re.I):
        return None
    match = re.fullmatch(
        r"(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
        r"(?:remember\s+(?:that\s+)?|(?:save|store)\s+(?:this\s+)?(?:as\s+)?(?:a\s+)?(?:preference|fact|personal\s+memory|memory)\s*:\s*)(.+)",
        message.strip(), re.I,
    )
    return match.group(1).strip(" .?!") if match else None
