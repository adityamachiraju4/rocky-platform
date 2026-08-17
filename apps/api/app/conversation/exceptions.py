"""Domain exceptions for the Conversation orchestration layer.

These describe why a message could not be turned into an executed action.
None of them are failures in the usual sense: a no-match or an ambiguous
reference is a normal, honest outcome that the service reports truthfully
without mutating anything.
"""
from __future__ import annotations


class ConversationError(Exception):
    """Base class for Conversation orchestration errors."""


class UnknownActionError(ConversationError):
    """The resolver proposed an action name absent from the closed registry.

    This is the trust boundary firing: the registry is code-owned, so any
    name not in it — whether a bug in the resolver or, later, an LLM
    hallucination — is refused before any service is touched.
    """

    def __init__(self, action: str) -> None:
        super().__init__(f"Unknown action: {action}")
        self.action = action


class NoMatchError(ConversationError):
    """The message did not resolve to any known entity.

    Reported honestly; nothing is mutated.
    """

    def __init__(self, message: str) -> None:
        super().__init__(f"No match for: {message}")
        self.message = message


class CompletionTargetNotFoundError(ConversationError):
    """The message clearly asked to complete a task, but no active task
    matched the extracted task reference. Nothing is mutated."""

    def __init__(self, target: str) -> None:
        super().__init__(f"No active task match for: {target}")
        self.target = target


class AmbiguousReferenceError(ConversationError):
    """The message matched more than one entity.

    Carries the candidate labels so the reply can surface them. Nothing is
    mutated: the caller must disambiguate.
    """

    def __init__(self, candidates: list[str]) -> None:
        super().__init__(f"Ambiguous reference: {candidates!r}")
        self.candidates = candidates
