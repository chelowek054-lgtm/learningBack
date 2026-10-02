"""Способы изучения: контракт и реестр (T-0053, T-0062, R-0029, R-0034).

Способ запоминания или проверки (карточки, письмо с оценкой, мини-игра) — самая изменчивая
часть системы, поэтому он подключается модулем и описывает себя здесь. Ядро не знает его
устройства и не вправе привязывать к нему граф или данные пользователя:

- на входе — узлы к проработке; на выходе — активности, которые способ предлагает;
- результат способ сообщает только как свидетельство об освоении (`core.evidence`) —
  освоенность принадлежит человеку и графу, а не способу, поэтому смена способа её не стирает;
- для одного шага изучения способов может быть несколько: ядро выбирает среди включённых.

Модуль объявляет способы методом `study_methods()` и заявляет возможность `study_methods`
в манифесте (C-0001). Отключили модуль — способы исчезли из выдачи, остальное работает.
"""

from __future__ import annotations

from dataclasses import dataclass

# Шаги изучения узла. Курс собирает цепочку из них и не знает, чем именно шаг исполняется.
READ = "read"
RECALL = "recall"
CONTRAST = "contrast"
APPLY = "apply"
REMEMBER = "remember"
PURPOSES = (READ, RECALL, CONTRAST, APPLY, REMEMBER)


class MethodError(ValueError):
    """Описание способа отклонено; `code` — для тестов и клиента."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StudyMethod:
    """Описание способа изучения.

    `activity_type` — тип активности, которым способ исполняется на клиенте; `offline` —
    можно ли им заниматься без сети (не каждый способ обязан работать офлайн, но тот,
    что работает, должен это заявить).
    """

    id: str
    title: str
    purpose: str
    activity_type: str
    offline: bool = False
    module: str = ""
    # Входит ли способ в цепочку шагов курса. Письмо с оценкой и задача на код — отдельные
    # упражнения: курс их не подставляет сам, а ведёт на них своими хуками.
    in_course: bool = True

    def describe(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "purpose": self.purpose,
            "activityType": self.activity_type,
            "offline": self.offline,
            "module": self.module,
            "inCourse": self.in_course,
        }


def check_methods(methods: list[StudyMethod]) -> None:
    """Проверить набор способов: уникальные id, известный шаг, непустой тип активности."""
    seen: set[str] = set()
    for m in methods:
        if not m.id or m.id in seen:
            raise MethodError("duplicate_method", f"Способ «{m.id}» объявлен дважды или без id")
        seen.add(m.id)
        if m.purpose not in PURPOSES:
            raise MethodError(
                "unknown_purpose",
                f"Способ «{m.id}»: неизвестный шаг «{m.purpose}», допустимы {', '.join(PURPOSES)}",
            )
        if not m.activity_type.strip():
            raise MethodError("no_activity_type", f"Способ «{m.id}» не назвал тип активности")
        if not m.title.strip():
            raise MethodError("no_title", f"Способ «{m.id}» без названия")


def for_purpose(
    methods: list[StudyMethod], purpose: str, preferred: str | None = None
) -> StudyMethod | None:
    """Способ для шага курса: предпочтительный, если он есть среди переданных, иначе первый."""
    candidates = [m for m in methods if m.purpose == purpose and m.in_course]
    if preferred is not None:
        chosen = next((m for m in candidates if m.id == preferred), None)
        if chosen is not None:
            return chosen
    return candidates[0] if candidates else None
