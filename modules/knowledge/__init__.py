"""Модуль `knowledge` — слой модели знаний (Фаза 2, 05-knowledge-model).

Самодостаточный пакет: свои ORM-модели, схемы, COW-чтение, centrality,
AI-роли и роутер. Ядро подключает модуль через `core.modules` и ничего не
знает о графе (инвариант №1 слоя). Зависимость направлена в одну сторону:
knowledge → core.

Таблицы делят общую `core.db.Base` — миграционная линия у проекта одна.
"""

from __future__ import annotations

from core.manifest import ModuleManifest
from core.modules import BackendModule

MODULE_ID = "knowledge"


class KnowledgeModule(BackendModule):
    id = MODULE_ID
    manifest = ModuleManifest(
        id=MODULE_ID,
        title="Граф знаний, курс и плейсмент",
        version="1.0",
        provides=frozenset({"routes", "admin_views"}),
        requires=frozenset(
            {"ai.structured", "data.activity", "data.response", "data.srs_card", "data.material"}
        ),
    )

    def router(self):
        from modules.knowledge.router import router

        return router

    def admin_views(self):
        from modules.knowledge.admin import VIEWS

        return VIEWS


backend = KnowledgeModule()

__all__ = ["MODULE_ID", "backend"]
