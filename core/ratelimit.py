"""Ограничитель частоты: скользящее окно в памяти процесса.

Хватает для одного экземпляра API. При нескольких воркерах счётчики у каждого
свои, и лимит фактически умножается на их число — тогда состояние нужно
переносить в общее хранилище (Redis/БД).
"""

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        """Учесть обращение; False — лимит за окно исчерпан (обращение не считается)."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


reset_request_limiter = SlidingWindowLimiter()
