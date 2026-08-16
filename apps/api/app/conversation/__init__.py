"""Conversation: the orchestration layer.

This package is deliberately NOT a peer capability. It sits one layer above
Projects, Tasks, and Activity and composes their services directly. The
dependency direction is:

    Conversation -> {Projects, Tasks, Activity} services -> Foundation -> Platform

Peer capabilities never depend upward on Conversation. Conversation owns no
ORM models, no tables, and no migration: it holds no state of its own. Its
sole job is to turn a natural-language message into a validated, executable
action and dispatch it into the authoritative domain services, preserving
every ownership, transaction, and Activity guarantee those services enforce.

The language-understanding brain (the resolver) is swappable behind a stable
Protocol. v1 ships a deterministic, intentionally dumb keyword resolver; an
LLM resolver can replace it later without moving the trust boundary, which
lives entirely in ConversationService, on the code-owned side.
"""
from app.conversation.router import router

__all__ = ["router"]
