"""Граф областей: реестр ключей, связи «нужно знать до», вычисляемый уровень (T-0064, R-0035, A-0022).

Граф понятий живёт внутри одной области; то, что лежит за её пределами, раньше молча считалось
известным. Здесь области становятся данными со своими связями: порядок изучения между областями
виден и считается, а не угадывается. Модуль не знает ни одного предмета: области — строки в базе.

Правила:
- ключ области устойчив, а разные названия одной области сводятся к нему через алиасы;
- «нужно знать до» не образует цикла и не ведёт область саму на себя;
- у нижней опоры (`foundation`) предпосылок нет — ниже граф не спускается;
- уровень примитивности не назначается: это глубина области в графе (опора — 0).
"""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy.orm import Session

from modules.knowledge.models import Domain, DomainAlias, DomainEdge


class DomainError(ValueError):
    """Нарушено правило графа областей; `code` — для тестов и клиента."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize(name: str) -> str:
    """Имя для сравнения: регистр, пробелы и знаки не должны плодить разные области."""
    return re.sub(r"[\W_]+", "-", (name or "").casefold(), flags=re.UNICODE).strip("-")


def resolve(session: Session, name: str) -> Domain | None:
    """Найти область по ключу, названию или алиасу; None — такой области ещё нет."""
    norm = normalize(name)
    if not norm:
        return None
    direct = session.get(Domain, norm)
    if direct is not None:
        return direct
    alias = session.get(DomainAlias, norm)
    return session.get(Domain, alias.domain_key) if alias else None


def register(
    session: Session,
    title: str,
    *,
    aliases: list[str] | None = None,
    foundation: bool = False,
) -> tuple[Domain, bool]:
    """Завести область или вернуть уже существующую; второе значение — создана ли новая.

    Если под названием или любым из алиасов область уже есть, новая не заводится: алиасы
    добавляются к найденной. Так одна область не размножается под разными именами.
    """
    key = normalize(title)
    if not key:
        raise DomainError("empty_title", "У области нет названия")
    names = [title, *(aliases or [])]
    found = next((d for d in (resolve(session, n) for n in names) if d is not None), None)
    created = found is None
    domain = found
    if domain is None:
        domain = Domain(key=key, title=title.strip(), foundation=foundation)
        session.add(domain)
        session.flush()
    for name in names:
        norm = normalize(name)
        if norm and norm != domain.key and session.get(DomainAlias, norm) is None:
            owner = session.get(Domain, norm)
            if owner is None:
                session.add(DomainAlias(alias=norm, domain_key=domain.key))
    session.flush()
    return domain, created


def _prereqs(session: Session) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for e in session.query(DomainEdge).all():
        out[e.domain_key].add(e.prereq_key)
    return out


def _reaches(prereqs: dict[str, set[str]], start: str, target: str) -> bool:
    """Достижимо ли `target` из `start` по связям «нужно знать до»."""
    stack, seen = [start], set()
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(prereqs.get(cur, ()))
    return False


def add_prereq(session: Session, domain_key: str, prereq_key: str) -> DomainEdge:
    """Связь «`prereq_key` нужно знать до `domain_key`». Повтор не дублирует."""
    if domain_key == prereq_key:
        raise DomainError("self_prereq", "Область не может требовать саму себя")
    domain, prereq = session.get(Domain, domain_key), session.get(Domain, prereq_key)
    if domain is None or prereq is None:
        raise DomainError("unknown_domain", "Неизвестная область")
    if domain.foundation:
        raise DomainError("foundation_has_no_prereqs", "У нижней опоры предпосылок нет")
    existing = session.get(DomainEdge, (domain_key, prereq_key))
    if existing is not None:
        return existing
    # Новая связь замкнёт цикл, если область уже лежит под своей будущей предпосылкой.
    if _reaches(_prereqs(session), prereq_key, domain_key):
        raise DomainError("cycle", "Связь замкнула бы цикл: порядок изучения стал бы невозможен")
    edge = DomainEdge(domain_key=domain_key, prereq_key=prereq_key)
    session.add(edge)
    session.flush()
    return edge


def levels(session: Session) -> dict[str, int]:
    """Уровень примитивности каждой области: опора и область без предпосылок — 0, иначе 1 + макс. по предпосылкам."""
    prereqs = _prereqs(session)
    keys = [d.key for d in session.query(Domain).all()]
    memo: dict[str, int] = {}

    def depth(key: str) -> int:
        if key not in memo:
            parents = prereqs.get(key, ())
            memo[key] = 1 + max(depth(p) for p in parents) if parents else 0
        return memo[key]

    return {k: depth(k) for k in keys}


def chain(session: Session, key: str) -> list[dict]:
    """Всё, что нужно знать до области `key`, от самого примитивного к ближайшему."""
    prereqs = _prereqs(session)
    lv = levels(session)
    below: set[str] = set()
    stack = list(prereqs.get(key, ()))
    while stack:
        cur = stack.pop()
        if cur in below:
            continue
        below.add(cur)
        stack.extend(prereqs.get(cur, ()))
    titles = {d.key: d.title for d in session.query(Domain).filter(Domain.key.in_(below or [""]))}
    ordered = sorted(below, key=lambda k: (lv[k], k))
    return [{"key": k, "title": titles[k], "level": lv[k]} for k in ordered]


def listing(session: Session) -> list[dict]:
    lv = levels(session)
    prereqs = _prereqs(session)
    aliases: dict[str, list[str]] = defaultdict(list)
    for a in session.query(DomainAlias).all():
        aliases[a.domain_key].append(a.alias)
    rows = [
        {
            "key": d.key,
            "title": d.title,
            "foundation": d.foundation,
            "level": lv[d.key],
            "prereqs": sorted(prereqs.get(d.key, ())),
            "aliases": sorted(aliases.get(d.key, ())),
        }
        for d in session.query(Domain).all()
    ]
    return sorted(rows, key=lambda r: (r["level"], r["key"]))
