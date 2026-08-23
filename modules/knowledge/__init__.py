"""Модуль `knowledge` — слой модели знаний (Фаза 2, 05-knowledge-model).

Самодостаточный пакет: свои ORM-модели, схемы, COW-чтение, centrality,
AI-роли и роутер. Ядро подключает только `router` и ничего не знает о графе
(инвариант №1 слоя). Зависимость направлена в одну сторону: knowledge → core.

Таблицы делят общую `core.db.Base` — миграционная линия у проекта одна.
"""

from modules.knowledge.router import router

MODULE_ID = "knowledge"

__all__ = ["MODULE_ID", "router"]
