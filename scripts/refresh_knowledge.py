"""Плановое обслуживание графа знаний: просроченные утверждения и проверка ссылок (T-0163, T-0164).

Запускать по расписанию (cron, планировщик): ставит в очередь две задачи, дальше их берёт обычный воркер.
    uv run python -m scripts.refresh_knowledge --user admin@example.com
"""

from __future__ import annotations

import argparse

from core.db import SessionLocal
from core.models import User
from modules.knowledge import freshness


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--user", required=True, help="почта администратора, от имени которого идут задачи"
    )
    args = p.parse_args()
    with SessionLocal() as session:
        user = session.query(User).filter(User.email == args.user).one_or_none()
        if user is None or not user.is_superuser:
            raise SystemExit("нужен существующий администратор")
        stale = freshness.stale_by_domain(session)
        freshness.enqueue_refresh(session, user.id)
        freshness.enqueue_links(session, user.id)
        session.commit()
    print(f"просрочено областей: {len(stale)}; задачи обновления и проверки ссылок поставлены")


if __name__ == "__main__":
    main()
