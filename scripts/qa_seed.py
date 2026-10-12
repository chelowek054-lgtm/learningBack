"""Данные для ручной проверки в браузере (V-0109…V-0140): ученик, предмет, курс, утверждения, проблема.

Тестовую базу можно ломать (решение пользователя); скрипт идемпотентен по почте ученика.
    uv run python -m scripts.qa_seed
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from core.db import SessionLocal
from core.models import User
from core.objects import MemoryObjectStore
from core.security import hash_password
from modules.knowledge import provenance
from modules.knowledge.course import generate_course
from modules.knowledge.models import (
    Claim,
    ClaimConflict,
    ClaimSource,
    Concept,
    ConceptTerm,
    GoalIntake,
)

DOMAIN = "python-data"
EMAIL = "qa-user@example.com"
PASSWORD = "QaPass-2026"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with SessionLocal() as s:
        user = s.query(User).filter_by(email=EMAIL).one_or_none()
        if user is None:
            user = User(email=EMAIL, password_hash=hash_password(PASSWORD), profile={})
            s.add(user)
            s.flush()
        user.profile = {
            "subject": {"id": DOMAIN, "title": "Python для анализа данных", "target": "apply"},
            "uiLang": "ru",
        }
        if not s.query(GoalIntake).filter_by(user_id=user.id, domain=DOMAIN).first():
            s.add(
                GoalIntake(
                    user_id=user.id,
                    domain=DOMAIN,
                    summary={"area": DOMAIN, "goal": DOMAIN, "level": "apply", "wishes": []},
                    confirmed_at=datetime.now(timezone.utc),
                )
            )
        # Часть понятий — «проверено», остальные остаются черновиками: видно обе пометки.
        concepts = s.query(Concept).filter_by(domain=DOMAIN).order_by(Concept.title).all()
        for c in concepts[:12]:
            c.status = provenance.APPROVED
        s.flush()

        # Утверждения с источниками и спорная пара — для источников в теории и пула проблем.
        store = MemoryObjectStore()
        doc, _ = provenance.add_document(
            s,
            title="Python Data Science Handbook",
            data=b"qa-handbook",
            domain=DOMAIN,
            license="cc-by",
            origin_url="https://example.org/handbook.pdf",
            store=store,
        )
        doc.lang, doc.trust_tier = "en", "A"
        doc2, _ = provenance.add_document(
            s,
            title="Старый конспект",
            data=b"qa-old",
            domain=DOMAIN,
            license="cc-by",
            origin_url="https://example.org/old",
            store=store,
        )
        doc2.lang, doc2.trust_tier = "ru", "B"
        f1 = provenance.add_fragments(
            s, doc, [{"text": "A list is an ordered mutable collection of items.", "page": 12}]
        )
        f2 = provenance.add_fragments(
            s, doc2, [{"text": "Список — неизменяемая последовательность.", "page": 3}]
        )
        target = concepts[0]
        old = Claim(
            concept_id=target.id,
            kind="definition",
            text="A list is an ordered mutable collection.",
            text_i18n={
                "en": "A list is an ordered mutable collection.",
                "ru": "Список — упорядоченная изменяемая коллекция.",
            },
            status="accepted",
            volatility="slow",
            as_of=datetime.now(timezone.utc) - timedelta(days=100),
        )
        new = Claim(
            concept_id=target.id,
            kind="definition",
            text="A list is an immutable sequence.",
            text_i18n={
                "en": "A list is an immutable sequence.",
                "ru": "Список — неизменяемая последовательность.",
            },
            status="disputed",
            volatility="slow",
        )
        s.add_all([old, new])
        s.flush()
        s.add(
            ClaimSource(
                claim_id=old.id,
                fragment_id=f1[0].id,
                quote="A list is an ordered mutable collection",
                verified=True,
            )
        )
        s.add(
            ClaimSource(
                claim_id=new.id,
                fragment_id=f2[0].id,
                quote="неизменяемая последовательность",
                verified=True,
            )
        )
        s.add(ClaimConflict(a_id=old.id, b_id=new.id, reason="изменяемость списка", status="open"))
        if not s.query(ConceptTerm).filter_by(concept_id=target.id, lang="ru").first():
            s.add(ConceptTerm(concept_id=target.id, lang="ru", term="Список (list)", primary=False))
        s.flush()
        generate_course(s, user.id, DOMAIN, "apply")
        s.commit()
        print("готово:", EMAIL, PASSWORD, DOMAIN, "узел со ссылками:", target.id, target.title)


if __name__ == "__main__":
    main()
