"""Pydantic-схемы API (Ф1). Sync-схемы — с camelCase-алиасами под клиентские TS-типы."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel

# --- Auth (WS1) ---


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    email: str | None
    profile: dict[str, Any]


class ProfileIn(BaseModel):
    profile: dict[str, Any]


# --- Sync (WS2) --- camelCase на проводе (совпадает с клиентскими типами ядра).


class _CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )


class ActivityIO(_CamelModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    module: str
    type: str
    connectivity: str
    payload: dict[str, Any]
    created_at: datetime | None = None
    due_at: datetime | None = None


class ResponseIO(_CamelModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    activity_id: uuid.UUID
    user_answer: Any
    grade: dict[str, Any] | None = None
    local_created_at: datetime
    synced: bool = False


class JobIO(_CamelModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    type: str
    status: str = "pending"
    input_ref: dict[str, Any]
    result: dict[str, Any] | None = None
    attempts: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SrsCardIO(_CamelModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    module: str
    front: dict[str, Any]
    back: dict[str, Any]
    source: str
    fsrs_state: dict[str, Any]
    due_at: datetime
    created_at: datetime | None = None


class SyncPushIn(_CamelModel):
    activities: list[ActivityIO] = []
    responses: list[ResponseIO] = []
    jobs: list[JobIO] = []
    srs_cards: list[SrsCardIO] = []


class SyncPushOut(_CamelModel):
    ack_ids: list[uuid.UUID]


class SyncPullOut(_CamelModel):
    activities: list[ActivityIO]
    responses: list[ResponseIO]
    jobs: list[JobIO]
    srs_cards: list[SrsCardIO]
