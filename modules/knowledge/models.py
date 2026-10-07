"""ORM-модели слоя знаний: граф концепций (канон + персонал, COW), оценки, курс.

Граф — ДАННЫЕ МОДУЛЯ, а не логика ядра (инвариант №1 слоя, 05-knowledge-model §10).
Таблицы живут в общей metadata (`core.db.Base`) — миграционная линия одна.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column

from core.db import TS as _ts
from core.db import Base, uuid_pk as _uuid_pk


class Concept(Base):
    """Канонический узел графа: контейнер формализованной теории."""

    __tablename__ = "concept"

    id: Mapped[uuid.UUID] = _uuid_pk()
    domain: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Устойчивый ключ узла внутри домена: по нему build/refresh находят узел,
    # даже если модель переименовала заголовок. Null — узел ещё не пересобирался.
    key: Mapped[str | None] = mapped_column(String, nullable=True)
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
    # Этап и уровень сложности (A-0030): метки, а не отдельные сущности. Null — понятие без этапа.
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    stage_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str | None] = mapped_column(String, nullable=True)  # basic|middle|advanced
    optional: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        Index("idx_concept_domain_tier", "domain", "tier"),
        Index(
            "uq_concept_domain_key",
            "domain",
            "key",
            unique=True,
            postgresql_where=text("key IS NOT NULL"),
        ),
    )


class ConceptEdge(Base):
    """Ребро канонического графа."""

    __tablename__ = "concept_edge"

    id: Mapped[uuid.UUID] = _uuid_pk()
    from_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id"), nullable=False)
    to_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id"), nullable=False)
    # prereq | specializes | part_of | related | contrasts | misconception | example
    type: Mapped[str] = mapped_column(String, nullable=False)
    # draft — внесено автоматически и не проверено; approved — подтверждено человеком;
    # rejected — отклонено с причиной (T-0076, R-0044).
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'draft'"))

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
    # Растёт с каждой правкой заголовка/теории: на версии держится кэш заданий.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
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


class GoalIntake(Base):
    """Подтверждённый итог диалога постановки цели (T-0061, R-0033).

    Хранится только пересказ «область, цель, уровень, пожелания», а не переписка:
    она нужна лишь затем, чтобы получить итог. Пока строки нет, граф области не строится.
    """

    __tablename__ = "goal_intake"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("uq_goal_intake_user_domain", "user_id", "domain", unique=True),)


class Domain(Base):
    """Область знаний в графе областей (T-0064, R-0035, A-0022).

    Ключ устойчив: по нему область находят и переиспользуют, а не строят заново. Уровень
    примитивности здесь не хранится — он вычисляется из связей (A-0022).
    """

    __tablename__ = "domain"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Нижняя опора: умения, ниже которых граф не спускается; предпосылок у неё нет.
    foundation: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())


class DomainAlias(Base):
    """Другое название области: «линал» и «линейная алгебра» ведут к одному ключу."""

    __tablename__ = "domain_alias"

    alias: Mapped[str] = mapped_column(String, primary_key=True)  # нормализованное имя
    domain_key: Mapped[str] = mapped_column(
        ForeignKey("domain.key", ondelete="CASCADE"), nullable=False
    )


class DomainEdge(Base):
    """«Нужно знать до»: область `prereq_key` предшествует области `domain_key`."""

    __tablename__ = "domain_edge"

    domain_key: Mapped[str] = mapped_column(
        ForeignKey("domain.key", ondelete="CASCADE"), primary_key=True
    )
    prereq_key: Mapped[str] = mapped_column(
        ForeignKey("domain.key", ondelete="CASCADE"), primary_key=True
    )


class ConceptLink(Base):
    """Предпосылка между понятиями РАЗНЫХ областей (T-0065, R-0036).

    Нужно не «вся область целиком», а конкретное понятие; `bloom` — ступень освоения зависимого
    понятия, с которой эта предпосылка обязательна (для цели «понять» она может не требоваться).
    """

    __tablename__ = "concept_link"

    id: Mapped[uuid.UUID] = _uuid_pk()
    from_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id"), nullable=False)
    to_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id"), nullable=False)
    bloom: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (
        Index("uq_concept_link_pair", "from_id", "to_id", unique=True),
        Index("idx_concept_link_to", "to_id"),
    )


# ---- происхождение знаний (T-0076, R-0043, R-0044, R-0045) ----


class SourceDocument(Base):
    """Документ-источник: учебник, статья, страница. Файл лежит в объектном хранилище."""

    __tablename__ = "source_document"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Ключ файла в объектном хранилище (core.objects); null — файл не сохранялся.
    object_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # Хэш содержимого: повторная загрузка того же файла не плодит дубли.
    content_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    origin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    license: Mapped[str | None] = mapped_column(String, nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    level: Mapped[str | None] = mapped_column(String, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())


class SourceFragment(Base):
    """Фрагмент документа: страница и заголовок главы. Ссылка на него — свидетельство утверждения."""

    __tablename__ = "source_fragment"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_source_fragment_doc", "document_id", "ordinal"),)


class ConceptSource(Base):
    """Понятие опирается на фрагмент: что именно из него взято (определение, пример, ошибка)."""

    __tablename__ = "concept_source"

    id: Mapped[uuid.UUID] = _uuid_pk()
    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept.id", ondelete="CASCADE"), nullable=False
    )
    fragment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_fragment.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'definition'"))

    __table_args__ = (Index("uq_concept_source", "concept_id", "fragment_id", "role", unique=True),)


class EdgeSource(Base):
    """Связь опирается на фрагмент: порядок изложения или прямое утверждение источника."""

    __tablename__ = "edge_source"

    id: Mapped[uuid.UUID] = _uuid_pk()
    edge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept_edge.id", ondelete="CASCADE"), nullable=False
    )
    fragment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_fragment.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (Index("uq_edge_source", "edge_id", "fragment_id", unique=True),)


class ReviewLog(Base):
    """Решения проверяющих: кто, когда и что сделал с понятием или связью (R-0046)."""

    __tablename__ = "review_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # concept | edge
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    # approve | reject | edit
    action: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("idx_review_log_target", "target_type", "target_id"),)


# ---- слияние понятий (T-0078) ----

EMBEDDING_DIM = 1024


class ConceptEmbedding(Base):
    """Вектор понятия для поиска близких: пересчитывается, когда меняется текст."""

    __tablename__ = "concept_embedding"

    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String, nullable=False)
    text_hash: Mapped[str] = mapped_column(String, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (
        Index(
            "idx_concept_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class MergeDecision(Base):
    """Решение модели по паре понятий: повторно за один и тот же вопрос не платим."""

    __tablename__ = "merge_decision"

    id: Mapped[uuid.UUID] = _uuid_pk()
    pair_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # same | refines | different | contradicts
    verdict: Mapped[str] = mapped_column(String, nullable=False)
    general: Mapped[str] = mapped_column(String, nullable=False, server_default=text("''"))
    reason: Mapped[str] = mapped_column(String, nullable=False, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())


class ConceptConflict(Base):
    """Противоречие между понятиями: очередь специалиста (R-0046), а не молчаливое решение."""

    __tablename__ = "concept_conflict"

    id: Mapped[uuid.UUID] = _uuid_pk()
    a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept.id", ondelete="CASCADE"), nullable=False
    )
    b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String, nullable=False, server_default=text("''"))
    # open | resolved
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'open'"))
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("uq_concept_conflict_pair", "a_id", "b_id", unique=True),)


class DomainSpecialist(Base):
    """Специалист по области: назначается администратором, проверяет знания только своих областей."""

    __tablename__ = "domain_specialist"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("uq_domain_specialist", "user_id", "domain", unique=True),)


class Notification(Base):
    """Уведомление человеку о его курсе: готов, дополнен, проверен (T-0083, R-0044)."""

    __tablename__ = "notification"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    # course_ready | course_extended | concept_verified | concept_changed
    kind: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)
    # Сколько понятий добавлено и сколько из них ещё не проверено: нужно, чтобы копить, а не плодить.
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(_ts, nullable=True)

    __table_args__ = (Index("idx_notification_user_unread", "user_id", "read_at"),)
