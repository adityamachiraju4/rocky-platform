"""Minimal synchronous in-process event dispatcher.

Publishers call publish(event); handlers registered for that event type run
synchronously in registration order. No broker, queue, store, or async.

Handler errors are LOGGED (not silently swallowed) and dispatch continues so
one misbehaving consumer cannot prevent others from receiving the event.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

from app.events.domain_events import DomainEvent

logger = logging.getLogger(__name__)

Handler = Callable[[DomainEvent], None]


class EventDispatcher:
    def __init__(self) -> None:
        self._subscribers: dict[type[DomainEvent], list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type[DomainEvent], handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event: DomainEvent) -> None:
        for handler in list(self._subscribers.get(type(event), ())):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Event handler %r failed while handling %s",
                    getattr(handler, "__name__", handler),
                    type(event).__name__,
                )
                # Cooperation: continue notifying remaining handlers.

    def clear(self) -> None:
        """Test hook: drop all subscriptions."""
        self._subscribers.clear()


# Process-wide default dispatcher. Capabilities publish here; consumers subscribe.
event_dispatcher = EventDispatcher()
