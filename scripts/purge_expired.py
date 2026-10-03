"""Удалить данные с истёкшим сроком хранения (R-0018). Запускать по расписанию, например раз в сутки:

    docker compose exec api uv run python -m scripts.purge_expired

Срок задаёт каждый тип данных (core.userdata.DataType.retention_days). Повторный запуск безопасен.
"""

from core import modules, userdata
from core.db import SessionLocal


def main() -> None:
    with SessionLocal() as session:
        modules.sync_module_state(session)
        purged = userdata.purge_expired(session, userdata.types(modules.enabled_modules()))
        session.commit()
    total = sum(purged.values())
    print(f"Удалено записей: {total}" + (f" ({purged})" if total else ""))


if __name__ == "__main__":
    main()
