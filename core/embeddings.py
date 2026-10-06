"""Эмбеддинги текста для поиска близких понятий (T-0078, A-0024).

Порт: список строк на входе, список векторов фиксированной длины на выходе. Провайдер — любой
сервис с совместимым `POST /embeddings` (по умолчанию `baai/bge-m3`: понимает русский и английский
вместе, 1024 числа). Без ключа работает детерминированная заглушка по хэшированным n-граммам
символов: она ловит почти одинаковые названия и нужна, чтобы слияние проверялось без сети и
без расходов; на смысловые пересказы она не рассчитана.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

from core.ai_base import ProviderError
from core.config import settings

BATCH = 64


class Embedder(Protocol):
    dim: int
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _norm(vec: list[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / length for x in vec]


class HashEmbedder:
    """Заглушка: n-граммы символов, свёрнутые хэшем в вектор; одинаковый текст — одинаковый вектор."""

    model = "hash-ngram"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            clean = re.sub(r"\s+", " ", text.lower()).strip()
            vec = [0.0] * self.dim
            padded = f"  {clean}  "
            for n in (3, 4):
                for i in range(len(padded) - n + 1):
                    h = int(hashlib.md5(padded[i : i + n].encode("utf-8")).hexdigest()[:8], 16)
                    vec[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
            out.append(_norm(vec))
        return out


class OpenAICompatibleEmbedder:
    def __init__(self) -> None:
        self.dim = settings.embedding_dim
        self.model = settings.embedding_model
        self._client = httpx.Client(
            base_url=settings.llm_base_url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            chunk = texts[start : start + BATCH]
            try:
                r = self._client.post("/embeddings", json={"model": self.model, "input": chunk})
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise ProviderError(f"Эмбеддинги недоступны: {exc}") from exc
            vectors = [d["embedding"] for d in data]
            if len(vectors) != len(chunk) or any(len(v) != self.dim for v in vectors):
                raise ProviderError(
                    f"Провайдер вернул векторы не той длины (ждали {self.dim}): смените EMBEDDING_DIM"
                )
            out += vectors
        return out


def get_embedder() -> Embedder:
    return OpenAICompatibleEmbedder() if settings.llm_api_key else HashEmbedder()
