"""Объектное хранилище источников (A-0025, T-0073, R-0045).

Ядро знает только контракт «ключ → байты» и временную ссылку для администратора. Реализация —
любой S3-совместимый сервис (SeaweedFS в compose): код через `boto3`, адрес и ключи из окружения,
поэтому замена хранилища бизнес-логику не трогает. Бакет закрытый: публичных адресов нет, файл
отдаётся только подписанной ссылкой с коротким сроком, и выдаёт её проверенный запрос админа.
Без `S3_ENDPOINT` работает хранилище в памяти — для тестов и разработки без сервиса.
"""

from __future__ import annotations

import logging
from typing import Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from core.config import settings

log = logging.getLogger("praxis.objects")

DEFAULT_LINK_TTL = 300


class ObjectNotFound(KeyError):
    """Ключа нет в хранилище."""


class ObjectStore(Protocol):
    def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def link(self, key: str, ttl: int = DEFAULT_LINK_TTL) -> str:
        """Временная ссылка на чтение; выдаётся только администратору."""
        ...


class MemoryObjectStore:
    """Хранилище в памяти процесса: контракт тот же, ссылка — условная."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._items[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        if key not in self._items:
            raise ObjectNotFound(key)
        return self._items[key][0]

    def exists(self, key: str) -> bool:
        return key in self._items

    def delete(self, key: str) -> None:
        self._items.pop(key, None)

    def link(self, key: str, ttl: int = DEFAULT_LINK_TTL) -> str:
        if key not in self._items:
            raise ObjectNotFound(key)
        return f"memory://{settings.s3_bucket}/{key}?ttl={ttl}"


class S3ObjectStore:
    """S3-совместимое хранилище. Ссылки подписываются публичным адресом, если он задан."""

    def __init__(self) -> None:
        def client(endpoint: str):
            return boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name=settings.s3_region,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )

        self._s3 = client(settings.s3_endpoint)
        # Подпись привязана к адресу, по которому админ откроет ссылку: он видит хранилище не так,
        # как API внутри сети compose.
        self._signer = client(settings.s3_public_endpoint or settings.s3_endpoint)
        self._bucket = settings.s3_bucket
        self._ensured = False

    def _ensure_bucket(self) -> None:
        if self._ensured:
            return
        try:
            self._s3.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._s3.create_bucket(Bucket=self._bucket)
        self._ensured = True

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._ensure_bucket()
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ObjectNotFound(key) from exc
            raise

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def link(self, key: str, ttl: int = DEFAULT_LINK_TTL) -> str:
        if not self.exists(key):
            raise ObjectNotFound(key)
        return self._signer.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=ttl
        )


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    """Настроенное хранилище; без `S3_ENDPOINT` — память (в развёрнутой среде это громко в логе)."""
    global _store
    if _store is None:
        if settings.s3_endpoint:
            _store = S3ObjectStore()
        else:
            if settings.is_deployed:
                log.error(
                    "S3_ENDPOINT не задан: источники хранятся в памяти и пропадут при перезапуске"
                )
            _store = MemoryObjectStore()
    return _store


def reset_store() -> None:
    """Для тестов: забыть выбранное хранилище."""
    global _store
    _store = None
