"""ORM-модели слоя знаний: граф концепций (канон + персонал, COW), оценки, курс.

Граф — ДАННЫЕ МОДУЛЯ, а не логика ядра (инвариант №1 слоя, 05-knowledge-model §10).
Таблицы живут в общей metadata (`core.db.Base`) — миграционная линия одна.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db import TS as _ts
from core.db import Base, uuid_pk as _uuid_pk


class Concept(Base):
    """Канонический узел графа: контейнер формализованной теории."""

    __tablename__ = "concept"

    id: Mapped[uuid.UUID] = _uuid_pk()
    domain: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'derived'")
    )  # core|derived
    centrality: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    content: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    bloom_levels: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    source: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'llm'"))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'draft'"))
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("idx_concept_domain_tier", "domain", "tier"),)


class ConceptEdge(Base):
    """Ребро канонического графа."""

    __tablename__ = "concept_edge"

    id: Mapped[uuid.UUID] = _uuid_pk()
    from_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id"), nullable=False)
    to_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id"), nullable=False)
    # prereq | specializes | part_of | related | contrasts | misconception | example
    type: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (Index("idx_concept_edge_from", "from_id"),)


class UserConcept(Base):
    """Персональный слой поверх канона (COW). base_concept_id=null → свой узел."""

    __tablename__ = "user_concept"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    # Домен обязателен: без него персональные узлы протекали в графы чужих областей.
    domain: Mapped[str] = mapped_column(String, nullable=False)
    base_concept_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("concept.id"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)  # для своих узлов
    content_override: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mastery: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'locked'"))
    origin: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'inherited'"))
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (
        Index("idx_user_concept_user", "user_id", "base_concept_id"),
        Index("idx_user_concept_user_domain", "user_id", "domain"),
    )


class UserEdge(Base):
    """Персональное ребро (рост под интересы)."""

    __tablename__ = "user_edge"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    from_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # concept.id | user_concept.id
    to_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("idx_user_edge_user", "user_id"),
        Index("idx_user_edge_user_domain", "user_id", "domain"),
    )


class Assessment(Base):
    """Сгенерённые из content узла тест-айтемы/практика (кэш, заземлён на версию)."""

    __tablename__ = "assessment"

    id: Mapped[uuid.UUID] = _uuid_pk()
    concept_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    concept_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # probe|test|practice
    bloom: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("idx_assessment_concept", "concept_id", "concept_version"),)


class Course(Base):
    """Сгенерированный курс: упорядоченный путь по узлам до цели."""

    __tablename__ = "course"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {concepts[], bloom}
    path: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    progress: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())
