"""Слить дубли понятий в области (T-0078): ручной запуск для администратора.

    uv run python -m scripts.merge_concepts <область> [--max-judgements 50]

Эмбеддинги отбирают кандидатов, модель решает по каждой паре; решения запоминаются, повторный
запуск по той же области почти ничего не стоит.
"""

from __future__ import annotations

import argparse

from core.ai_gateway import get_ai_gateway
from core.db import SessionLocal
from modules.knowledge import merge


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    parser.add_argument("--max-judgements", type=int)
    args = parser.parse_args()
    with SessionLocal() as session:
        report = merge.merge_domain(
            session, args.domain, get_ai_gateway(), max_judgements=args.max_judgements
        )
        session.commit()
    print(report)


if __name__ == "__main__":
    main()
