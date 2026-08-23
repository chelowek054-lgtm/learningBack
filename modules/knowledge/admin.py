"""Представления слоя знаний для админки.

Модуль сам описывает, как показывать свои таблицы; ядро только собирает
`VIEWS` в `core.admin.setup_admin`. Так админка растёт вместе с модулями,
не втягивая предметное знание в ядро.
"""

from __future__ import annotations

from sqladmin import ModelView, action
from sqladmin.filters import AllUniqueStringValuesFilter
from starlette.requests import Request
from starlette.responses import RedirectResponse

from core.db import SessionLocal
from modules.knowledge.centrality import recompute_centrality
from modules.knowledge.models import (
    Assessment,
    Concept,
    ConceptEdge,
    Course,
    UserConcept,
    UserEdge,
)

_CATEGORY = "Модель знаний"


def _selected(request: Request) -> list[str]:
    raw = request.query_params.get("pks", "")
    return [pk for pk in raw.split(",") if pk]


def _back(request: Request, identity: str) -> RedirectResponse:
    referer = request.headers.get("referer")
    url = referer or request.url_for("admin:list", identity=identity)
    return RedirectResponse(url, status_code=302)


class ConceptAdmin(ModelView, model=Concept):
    name = "Концепция (канон)"
    name_plural = "Концепции (канон)"
    icon = "fa-solid fa-diagram-project"
    category = _CATEGORY
    column_list = [
        Concept.domain,
        Concept.title,
        Concept.tier,
        Concept.centrality,
        Concept.status,
        Concept.version,
    ]
    column_searchable_list = [Concept.title]
    column_sortable_list = [Concept.centrality, Concept.title, Concept.tier]
    column_filters = [
        AllUniqueStringValuesFilter(Concept.domain, title="Домен"),
        AllUniqueStringValuesFilter(Concept.tier, title="Тир"),
        AllUniqueStringValuesFilter(Concept.status, title="Статус"),
        AllUniqueStringValuesFilter(Concept.source, title="Источник"),
    ]
    page_size = 50
    page_size_options = [25, 50, 100, 200]

    # ---- курирование канона прямо из панели ----

    @action(
        name="approve_nodes",
        label="Одобрить (status=approved)",
        confirmation_message="Одобрить выбранные узлы?",
    )
    async def approve_nodes(self, request: Request) -> RedirectResponse:
        with SessionLocal() as session:
            for pk in _selected(request):
                c = session.get(Concept, pk)
                if c is not None:
                    c.status = "approved"
            session.commit()
        return _back(request, self.identity)

    @action(
        name="mark_core",
        label="В ядро (tier=core)",
        confirmation_message="Пометить выбранные узлы фундаментальным ядром?",
    )
    async def mark_core(self, request: Request) -> RedirectResponse:
        return self._set_tier(request, "core")

    @action(
        name="mark_derived",
        label="В ветви (tier=derived)",
        confirmation_message="Перевести выбранные узлы в вытекающие ветви?",
    )
    async def mark_derived(self, request: Request) -> RedirectResponse:
        return self._set_tier(request, "derived")

    def _set_tier(self, request: Request, tier: str) -> RedirectResponse:
        with SessionLocal() as session:
            for pk in _selected(request):
                c = session.get(Concept, pk)
                if c is not None:
                    c.tier = tier
            session.commit()
        return _back(request, self.identity)

    @action(
        name="recompute_centrality",
        label="Пересчитать centrality домена",
        confirmation_message="Пересчитать centrality для доменов выбранных узлов?",
    )
    async def recompute_action(self, request: Request) -> RedirectResponse:
        with SessionLocal() as session:
            domains = {
                c.domain
                for c in (session.get(Concept, pk) for pk in _selected(request))
                if c is not None
            }
            for domain in domains:
                recompute_centrality(session, domain)
        return _back(request, self.identity)


class ConceptEdgeAdmin(ModelView, model=ConceptEdge):
    name_plural = "Связи канона"
    icon = "fa-solid fa-arrows-left-right"
    category = _CATEGORY
    column_list = [ConceptEdge.from_id, ConceptEdge.to_id, ConceptEdge.type]
    column_filters = [AllUniqueStringValuesFilter(ConceptEdge.type, title="Тип связи")]


class UserConceptAdmin(ModelView, model=UserConcept):
    name_plural = "Персональные узлы (COW)"
    icon = "fa-solid fa-user-pen"
    category = _CATEGORY
    column_list = [
        UserConcept.user_id,
        UserConcept.base_concept_id,
        UserConcept.title,
        UserConcept.origin,
        UserConcept.status,
    ]
    column_searchable_list = [UserConcept.title]
    column_filters = [
        AllUniqueStringValuesFilter(UserConcept.origin, title="Происхождение"),
        AllUniqueStringValuesFilter(UserConcept.status, title="Статус"),
    ]


class UserEdgeAdmin(ModelView, model=UserEdge):
    name_plural = "Персональные связи"
    icon = "fa-solid fa-share-nodes"
    category = _CATEGORY
    column_list = [UserEdge.user_id, UserEdge.from_id, UserEdge.to_id, UserEdge.type]


class AssessmentAdmin(ModelView, model=Assessment):
    name_plural = "Оценки (кэш)"
    icon = "fa-solid fa-clipboard-question"
    category = _CATEGORY
    column_list = [
        Assessment.concept_id,
        Assessment.concept_version,
        Assessment.kind,
        Assessment.bloom,
    ]
    column_filters = [
        AllUniqueStringValuesFilter(Assessment.kind, title="Вид"),
        AllUniqueStringValuesFilter(Assessment.bloom, title="Ступень Блума"),
    ]


class CourseAdmin(ModelView, model=Course):
    name_plural = "Курсы"
    icon = "fa-solid fa-route"
    category = _CATEGORY
    column_list = [Course.user_id, Course.domain, Course.created_at]


VIEWS = [
    ConceptAdmin,
    ConceptEdgeAdmin,
    UserConceptAdmin,
    UserEdgeAdmin,
    AssessmentAdmin,
    CourseAdmin,
]
