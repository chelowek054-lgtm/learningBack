"""Pydantic-схемы запросов слоя знаний (KG1-KG2)."""

from typing import Any
import uuid

from pydantic import BaseModel, Field

from modules.knowledge.content import NodeContent


class BuildGraphIn(BaseModel):
    domain: str
    topic: str = ""


class CanonNodeIn(BaseModel):
    domain: str
    title: str
    tier: str = "derived"
    content: NodeContent = Field(default_factory=NodeContent)
    bloom_levels: list[str] = Field(default_factory=list)
    difficulty: int = 1
    source: str = "curated"
    centrality: float = 0.0


class CanonNodePatch(BaseModel):
    title: str | None = None
    tier: str | None = None
    content: NodeContent | None = None
    centrality: float | None = None
    status: str | None = None


class CanonEdgeIn(BaseModel):
    from_id: uuid.UUID
    to_id: uuid.UUID
    type: str


class OwnNodeIn(BaseModel):
    domain: str
    title: str
    content: NodeContent = Field(default_factory=NodeContent)


class OverrideIn(BaseModel):
    content: NodeContent


class UserNodePatch(BaseModel):
    title: str | None = None
    content: NodeContent | None = None
    mastery: dict[str, Any] | None = None
    status: str | None = None


class UserEdgeIn(BaseModel):
    domain: str
    from_id: uuid.UUID
    to_id: uuid.UUID
    type: str


class ApproveNodeIn(BaseModel):
    tier: str | None = None  # подтвердить/сменить тир при апруве


class RecomputeIn(BaseModel):
    domain: str


class ExpandIn(BaseModel):
    concept_id: uuid.UUID
    direction: str


class PlacementAnswerIn(BaseModel):
    domain: str
    concept_id: uuid.UUID
    bloom: str
    answer: Any = None
