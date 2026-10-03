"""Фоновый воркер AI-задач. Запуск (в compose это отдельный сервис `worker`):

    docker compose up -d worker
    uv run python -m scripts.worker

Работает вместе с `JOBS_MODE=worker` у API: тогда /sync/push только ставит задачи в очередь.
"""

import logging
import signal

from core import worker

_stop = False


def _on_signal(*_: object) -> None:
    global _stop
    _stop = True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    done = worker.run_loop(should_stop=lambda: _stop)
    print(f"Воркер остановлен, выполнено задач: {done}")


if __name__ == "__main__":
    main()
