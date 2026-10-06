"""Объектное хранилище источников: контракт и подписанные ссылки (T-0073, A-0025)."""

from __future__ import annotations

import socket
import urllib.request
import uuid
from urllib.parse import urlparse

import pytest

from core import objects
from core.config import settings
from core.objects import MemoryObjectStore, ObjectNotFound, S3ObjectStore

S3_URL = "http://localhost:8333"


def _s3_up() -> bool:
    try:
        with socket.create_connection(("localhost", 8333), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(params=["memory", "seaweedfs"])
def store(request, monkeypatch):
    if request.param == "memory":
        return MemoryObjectStore()
    if not _s3_up():
        pytest.skip("SeaweedFS не запущен: docker compose up -d seaweedfs")
    for name, value in {
        "s3_endpoint": S3_URL,
        "s3_public_endpoint": S3_URL,
        "s3_access_key": "praxis",
        "s3_secret_key": "praxis-secret",
        "s3_bucket": "praxis-test",
    }.items():
        monkeypatch.setattr(settings, name, value)
    return S3ObjectStore()


def test_put_get_exists_delete(store):
    key = f"books/{uuid.uuid4().hex}.pdf"
    assert store.exists(key) is False
    store.put(key, b"%PDF-1.4 fake", "application/pdf")
    assert store.exists(key) is True and store.get(key) == b"%PDF-1.4 fake"
    store.delete(key)
    assert store.exists(key) is False
    with pytest.raises(ObjectNotFound):
        store.get(key)


def test_link_exists_only_for_stored_keys(store):
    with pytest.raises(ObjectNotFound):
        store.link("nope/none.pdf")
    store.put("a/b.txt", b"hello")
    assert store.link("a/b.txt", ttl=60)


def test_presigned_link_reads_the_file_and_is_signed(store):
    if isinstance(store, MemoryObjectStore):
        pytest.skip("подпись есть только у S3")
    store.put("docs/x.txt", b"secret source")
    url = store.link("docs/x.txt", ttl=60)
    assert "X-Amz-Signature" in url and "X-Amz-Expires=60" in url
    assert urllib.request.urlopen(url, timeout=10).read() == b"secret source"  # noqa: S310
    # Без подписи бакет закрыт.
    bare = f"{urlparse(url).scheme}://{urlparse(url).netloc}{urlparse(url).path}"
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(bare, timeout=10)  # noqa: S310
    assert err.value.code in (401, 403)


def test_without_endpoint_the_memory_store_is_used(monkeypatch):
    monkeypatch.setattr(settings, "s3_endpoint", "")
    objects.reset_store()
    try:
        assert isinstance(objects.get_store(), MemoryObjectStore)
    finally:
        objects.reset_store()
