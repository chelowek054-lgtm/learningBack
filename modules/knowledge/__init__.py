"""Модуль `knowledge` — слой модели знаний (Фаза 2, 05-knowledge-model).

Самодостаточный пакет: свои ORM-модели, схемы, COW-чтение, centrality,
AI-роли и роутер. Ядро подключает модуль через `core.modules` и ничего не
знает о графе (инвариант №1 слоя). Зависимость направлена в одну сторону:
knowledge → core.

Таблицы делят общую `core.db.Base` — миграционная линия у проекта одна.
"""

from __future__ import annotations

from core.manifest import ModuleManifest
from core.methods import APPLY, CONTRAST, READ, RECALL, StudyMethod
from core.modules import BackendModule

MODULE_ID = "knowledge"


class KnowledgeModule(BackendModule):
    id = MODULE_ID
    manifest = ModuleManifest(
        id=MODULE_ID,
        title="Граф знаний, курс и плейсмент",
        version="1.0",
        provides=frozenset(
            {"routes", "admin_views", "study_methods", "evidence", "study_preferences"}
        ),
        requires=frozenset(
            {"ai.structured", "data.activity", "data.response", "data.srs_card", "data.material"}
        ),
    )

    def study_methods(self):
        """Способы шагов курса по графу: прочитать теорию, вспомнить, отличить, применить."""
        return [
            StudyMethod("concept_study", "Теория узла", READ, "concept_study", offline=True),
            StudyMethod("concept_recall", "Вспомнить понятие", RECALL, "concept_recall"),
            StudyMethod(
                "concept_contrast", "Отличить от заблуждения", CONTRAST, "concept_contrast"
            ),
            StudyMethod("concept_apply", "Применить понятие", APPLY, "concept_apply"),
        ]

    def accept_evidence(self, session, user_id, domain, evidence):
        """Освоенность живёт в графе: свидетельство любого способа учитывается здесь."""
        from modules.knowledge import api

        api.record_evidence(session, user_id, domain, evidence)

    def study_methods_changed(self, session, user_id) -> None:
        """Курс собран из способов: после смены выбора пересобираем его, прогресс и освоенность целы."""
        import uuid

        from modules.knowledge.course import generate_course
        from modules.knowledge.models import Course

        for course in session.query(Course).filter_by(user_id=user_id).all():
            target = course.target or {}
            bloom = target.get("bloom")
            if not bloom:
                continue
            interests = [uuid.UUID(i) for i in target.get("concepts") or []]
            generate_course(session, user_id, course.domain, bloom, interests)

    def router(self):
        from modules.knowledge.router import router

        return router

    def admin_views(self):
        from modules.knowledge.admin import VIEWS

        return VIEWS


backend = KnowledgeModule()

__all__ = ["MODULE_ID", "backend"]
