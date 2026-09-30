"""Модуль `knowledge` — слой модели знаний (Фаза 2, 05-knowledge-model).

Самодостаточный пакет: свои ORM-модели, схемы, COW-чтение, centrality,
AI-роли и роутер. Ядро подключает модуль через `core.modules` и ничего не
знает о графе (инвариант №1 слоя). Зависимость направлена в одну сторону:
knowledge → core.

Таблицы делят общую `core.db.Base` — миграционная линия у проекта одна.
"""

from __future__ import annotations

from core.modules import BackendModule

MODULE_ID = "knowledge"


class KnowledgeModule(BackendModule):
    id = MODULE_ID

    def router(self):
        from modules.knowledge.router import router

        return router

    def admin_views(self):
        from modules.knowledge.admin import VIEWS

        return VIEWS


backend = KnowledgeModule()

__all__ = ["MODULE_ID", "backend"]
