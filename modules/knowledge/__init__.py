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
    first_party = True
    manifest = ModuleManifest(
        id=MODULE_ID,
        title="Граф знаний, курс и плейсмент",
        version="1.0",
        provides=frozenset(
            {
                "routes",
                "admin_views",
                "study_methods",
                "evidence",
                "study_preferences",
                "job_handlers",
            }
        ),
        requires=frozenset(
            {
                "ai.structured",
                "data.activity",
                "data.response",
                "data.srs_card",
                "data.material",
                "data.mastery",
                "data.course",
                "data.goal",
            }
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

    def data_types(self):
        """Данные человека, которыми владеет граф знаний: освоенность, курс, подтверждённая цель."""
        from core.userdata import DataType, model_type
        from modules.knowledge.models import Course, GoalIntake, UserConcept, UserEdge

        mastery = model_type(
            UserConcept,
            "mastery",
            "Освоенность и личный граф",
            "knowledge",
            "Оценка освоения понятий и персональные узлы",
            None,
        )
        edges = model_type(UserEdge, "_edges", "", "knowledge", "", None)

        def read(session, user_id):
            return mastery.read(session, user_id) + [
                {"edge": r} for r in edges.read(session, user_id)
            ]

        def erase(session, user_id):
            return edges.erase(session, user_id) + mastery.erase(session, user_id)

        return [
            DataType("mastery", mastery.title, "knowledge", mastery.purpose, None, read, erase),
            model_type(Course, "course", "Курс", "knowledge", "Путь по графу до цели", None),
            model_type(
                GoalIntake,
                "goal",
                "Подтверждённая цель",
                "knowledge",
                "Итог диалога постановки цели",
                None,
            ),
        ]

    def router(self):
        from modules.knowledge.router import router

        return router

    def admin_views(self):
        from modules.knowledge.admin import VIEWS

        return VIEWS

    def job_handlers(self):
        """Разбор документа в понятия идёт фоновой задачей (T-0077), а не на запросе человека."""
        from modules.knowledge.ingest import JOB_TYPE, ingest_job

        return {JOB_TYPE: ingest_job}


backend = KnowledgeModule()

__all__ = ["MODULE_ID", "backend"]
