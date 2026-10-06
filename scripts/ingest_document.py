"""Загрузить документ в источники и разобрать его в граф (T-0077): ручной запуск для администратора.

    uv run python -m scripts.ingest_document книга.pdf --domain algebra [--title "Algebra 1"] [--level B2]

Файл сохраняется в объектное хранилище, разбирается в понятия со ссылками на фрагменты; всё
внесённое — «черновик» до проверки человеком. Повторный запуск того же файла ничего не тратит.
Запросы к модели идут напрямую (не через очередь), чтобы разбор можно было смотреть вживую.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.ai_gateway import get_ai_gateway
from core.db import SessionLocal
from modules.knowledge import ingest, provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--title")
    parser.add_argument("--level")
    parser.add_argument("--license")
    args = parser.parse_args()

    path = Path(args.path)
    with SessionLocal() as session:
        doc, created = provenance.add_document(
            session,
            title=args.title or path.stem,
            data=path.read_bytes(),
            domain=args.domain,
            level=args.level,
            license=args.license,
            meta={"filename": path.name},
        )
        print(("Добавлен" if created else "Уже есть") + f": {doc.title} ({doc.id})")
        state = ingest.ingest(session, doc, get_ai_gateway())
        session.commit()
    print(state)


if __name__ == "__main__":
    main()
