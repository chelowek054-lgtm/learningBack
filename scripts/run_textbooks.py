"""Прогон нескольких учебников через весь конвейер знаний: от файла до собранных узлов.

    uv run python -m scripts.run_textbooks книги/ --domain probability --license cc-by --user admin@example.com

Для каждого файла: приём (лицензия, язык, воронка отсева, уровень доверия) → извлечение утверждений с
цитатами → слияние понятий по всему графу → сверка утверждений узла (противоречия — в пул проблем) →
сборка узла: теория на двух языках и банк заданий. Очередь задач разбирается здесь же, по порядку,
запросы к модели идут напрямую; отчёт печатается по ходу и итоговый — в конце.
Повторный запуск тех же файлов ничего не тратит: готовое пропускается.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from sqlalchemy import func

from core.ai_gateway import get_ai_gateway
from core.db import SessionLocal
from core.models import Job, User
from core.worker import work_one
from modules.knowledge import ingest, provenance
from modules.knowledge.models import (
    Claim,
    ClaimConflict,
    Concept,
    ConceptArea,
    LearningItem,
    SourceDocument,
)

SUFFIXES = {".pdf", ".txt", ".md", ".epub", ".docx", ".html"}


def _files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out += sorted(x for x in p.iterdir() if x.suffix.lower() in SUFFIXES)
        elif p.exists():
            out.append(p)
    return out


def _drain(gateway, quiet: bool = False) -> int:
    """Разобрать очередь до конца: ждущие повтора задачи дожидаемся, но не бесконечно."""
    done, idle = 0, 0
    while True:
        with SessionLocal() as session:
            job = work_one(session, gateway)
            session.commit()
            if job is not None:
                done += 1
                idle = 0
                if not quiet:
                    print(f"  [{job.type}] {job.status}: {job.result}")
                continue
            waiting = session.query(Job).filter(Job.status.in_(("pending", "running"))).count()
        if not waiting:
            return done
        idle += 1
        if idle > 60:  # около пяти минут ожидания повтора
            print("Остались задачи, ждущие повтора; остановка.")
            return done
        time.sleep(5)


def _report() -> None:
    with SessionLocal() as s:
        print("\n=== Итог ===")
        for doc in s.query(SourceDocument).order_by(SourceDocument.created_at).all():
            state = (doc.meta or {}).get("ingest") or {}
            print(
                f"- {doc.title}: язык={doc.lang} доверие={doc.trust_tier} "
                f"карантин={doc.quarantined} окон={state.get('done')}/{state.get('windows')} "
                f"понятий={state.get('concepts')} отброшено={state.get('dropped')}"
            )
            for reason in doc.trust_reasons or []:
                print(f"    · {reason}")
        by_status = dict(s.query(Claim.status, func.count()).group_by(Claim.status).all())
        print(
            f"Понятий: {s.query(Concept).count()} (в нескольких областях: "
            f"{s.query(ConceptArea.concept_id).group_by(ConceptArea.concept_id).having(func.count() > 1).count()})"
        )
        print(f"Утверждений: {by_status}")
        print(f"Проблем в пуле: {s.query(ClaimConflict).filter_by(status='open').count()}")
        print(f"Заданий в банке: {s.query(LearningItem).count()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="файлы или папки с учебниками")
    parser.add_argument("--domain", required=True)
    parser.add_argument(
        "--license", help="лицензия источников: cc-by, cc-by-sa, public-domain, owned, ..."
    )
    parser.add_argument("--level")
    parser.add_argument(
        "--user", required=True, help="email администратора: на него ставятся задачи и расход"
    )
    args = parser.parse_args()

    files = _files(args.paths)
    if not files:
        raise SystemExit("Файлов не найдено")
    gateway = get_ai_gateway()
    with SessionLocal() as session:
        user = session.query(User).filter_by(email=args.user).one_or_none()
        if user is None:
            raise SystemExit(f"Пользователь {args.user} не найден")
        user_id = user.id
        for path in files:
            doc, created = provenance.add_document(
                session,
                title=path.stem,
                data=path.read_bytes(),
                added_by=user_id,
                domain=args.domain,
                level=args.level,
                license=args.license,
                meta={"filename": path.name},
            )
            print(("Добавлен" if created else "Уже есть") + f": {doc.title} ({doc.id})")
            ingest.enqueue_ingest(session, doc, user_id)
        session.commit()

    print("Разбор очереди…")
    _drain(gateway)
    _report()


if __name__ == "__main__":
    main()
