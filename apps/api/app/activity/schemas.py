"""Pydantic v2 schemas for the Activity capability.

There is deliberately no write schema: activity is emitted by the domain
services, never created through the public API. ``event_type`` is exposed as a
plain ``str`` on the read side — the closed set is enforced at the recorder's
Python boundary, not at the HTTP edge (no client ever supplies it).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    event_type: str
    entity_type: str
    entity_id: uuid.UUID
    payload: dict[str, Any]
    created_at: datetime
