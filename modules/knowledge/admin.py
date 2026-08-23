"""Представления слоя знаний для админки.

Модуль сам описывает, как показывать свои таблицы; ядро только собирает
`VIEWS` в `core.admin.setup_admin`. Так админка растёт вместе с модулями,
не втягивая предметное знание в ядро.
"""

from __future__ import annotations

from sqladmin import ModelView

from modules.knowledge.models import (
    Assessment,
    Concept,
    ConceptEdge,
    Course,
    UserConcept,
    UserEdge,
)

_CATEGORY = "Модель знаний"


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


class ConceptEdgeAdmin(ModelView, model=ConceptEdge):
    name_plural = "Связи канона"
    icon = "fa-solid fa-arrows-left-right"
    category = _CATEGORY
    column_list = [ConceptEdge.from_id, ConceptEdge.to_id, ConceptEdge.type]


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
