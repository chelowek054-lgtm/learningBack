"""Префикс /v1 и проверка совместимости клиента (T-0033, R-0020)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app import app
from core.config import settings
from core.versioning import is_outdated, parse_version


@pytest.fixture
def anon():
    return TestClient(app)


@pytest.fixture
def min_version(monkeypatch):
    monkeypatch.setattr(settings, "min_client_version", "1.2.0")


# ---- разбор версий ----


@pytest.mark.parametrize(
    "text,expected",
    [("1.2.3", (1, 2, 3)), ("1.2", (1, 2, 0)), ("v2", (2, 0, 0)), ("1.2.3-beta.4", (1, 2, 3))],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "x.y"])
def test_unparseable_version_is_none(text):
    assert parse_version(text) is None


def test_comparison_is_numeric_not_textual():
    assert is_outdated("1.9.0", "1.10.0") is True  # строкой «1.9» > «1.10»
    assert is_outdated("1.10.0", "1.9.0") is False
    assert is_outdated("1.2.0", "1.2.0") is False


def test_unknown_version_is_not_treated_as_outdated():
    assert is_outdated("garbage", "1.2.0") is False
    assert is_outdated("1.0.0", "garbage") is False


# ---- префикс ----


def test_api_is_served_under_v1(anon):
    assert anon.get("/v1/version").status_code == 200
    # Маршрут есть: без токена отказ по авторизации, а не 404. БД не нужна.
    assert anon.get("/v1/auth/me").status_code in (401, 403)


def test_legacy_unprefixed_paths_still_work_for_existing_builds(anon):
    assert anon.get("/auth/me").status_code in (401, 403)


def test_openapi_documents_only_v1_paths():
    paths = app.openapi()["paths"]

    assert any(p.startswith("/v1/auth") for p in paths)
    assert not any(p.startswith("/auth") for p in paths)  # устаревшие дубли в схему не попадают


def test_health_stays_unversioned_for_probes(anon):
    assert anon.get("/health").json()["status"] == "ok"


def test_responses_carry_api_version(anon):
    assert anon.get("/v1/version").headers["x-api-version"] == "1"
    assert anon.get("/health").headers["x-api-version"] == "1"


def test_version_endpoint_reports_floor(anon, min_version):
    body = anon.get("/v1/version").json()

    assert body["apiVersion"] == "1" and body["minClientVersion"] == "1.2.0"


# ---- совместимость клиента ----


def test_outdated_client_gets_426_with_a_readable_reason(anon, min_version):
    r = anon.get("/v1/auth/me", headers={"X-Client-Version": "1.1.9"})

    assert r.status_code == 426
    body = r.json()
    assert body["code"] == "client_outdated" and body["minClientVersion"] == "1.2.0"
    assert "обновите" in body["detail"].lower()


def test_current_client_passes_through(anon, min_version):
    r = anon.get("/v1/auth/me", headers={"X-Client-Version": "1.2.0"})

    assert r.status_code in (401, 403)  # дошло до авторизации, версия не помешала


def test_request_without_version_header_is_allowed(anon, min_version):
    assert anon.get("/v1/auth/me").status_code in (401, 403)


def test_outdated_client_can_still_read_version_and_health(anon, min_version):
    old = {"X-Client-Version": "0.1.0"}

    assert anon.get("/v1/version", headers=old).status_code == 200
    assert anon.get("/health", headers=old).status_code == 200


def test_default_floor_blocks_nobody(anon):
    assert anon.get("/v1/auth/me", headers={"X-Client-Version": "0.0.1"}).status_code in (401, 403)
