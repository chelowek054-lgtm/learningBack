"""Резервная копия объектного хранилища: копия сверяется с манифестом (T-0073)."""

from __future__ import annotations

import json

import pytest

from core import objects
from core.config import settings
from core.objects import S3ObjectStore
from scripts import backup_objects
from tests.test_objects import S3_URL, _s3_up


@pytest.fixture
def seaweed(monkeypatch):
    if not _s3_up():
        pytest.skip("SeaweedFS не запущен: docker compose up -d seaweedfs")
    for name, value in {
        "s3_endpoint": S3_URL,
        "s3_public_endpoint": S3_URL,
        "s3_access_key": "praxis",
        "s3_secret_key": "praxis-secret",
        "s3_bucket": "praxis-backup-test",
    }.items():
        monkeypatch.setattr(settings, name, value)
    objects.reset_store()
    store = objects.get_store()
    assert isinstance(store, S3ObjectStore)
    yield store
    for key in backup_objects._keys():  # noqa: SLF001
        store.delete(key)
    objects.reset_store()


def test_backup_then_verify_passes_and_detects_damage(seaweed, tmp_path):
    seaweed.put("books/a.pdf", b"AAA")
    seaweed.put("books/b.pdf", b"BBBB")
    assert backup_objects.main([str(tmp_path)]) == 0
    copy = next(tmp_path.glob("objects-*"))
    assert {i["key"] for i in json.loads((copy / "manifest.json").read_text())} == {
        "books/a.pdf",
        "books/b.pdf",
    }
    assert backup_objects.main(["--verify", str(tmp_path)]) == 0

    (copy / "files" / "books" / "a.pdf").write_bytes(b"damaged")
    assert backup_objects.main(["--verify", str(tmp_path)]) == 1

    (copy / "files" / "books" / "a.pdf").write_bytes(b"AAA")
    seaweed.delete("books/b.pdf")
    assert backup_objects.main(["--verify", str(tmp_path)]) == 1
