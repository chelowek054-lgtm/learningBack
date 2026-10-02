"""Манифест модуля, версия контракта и жизненный цикл (T-0051, C-0001, V-0079)."""

from __future__ import annotations

import pytest
from fastapi import APIRouter

from core import modules
from core.manifest import (
    CONTRACT_VERSION,
    ManifestError,
    ModuleManifest,
    check_manifest,
    check_set,
    contract_compatible,
    load_order,
)
from core.models import ModuleState, Rubric
from tests.conftest import make_user


def M(id="alpha", **over):
    base = {"id": id, "title": "Модуль", "version": "1.0", "contract": CONTRACT_VERSION}
    return ModuleManifest(**{**base, **over})


def code_of(fn, *a, **k):
    with pytest.raises(ManifestError) as e:
        fn(*a, **k)
    return e.value.code, str(e.value)


# ---- один манифест ----


def test_valid_manifest_passes():
    check_manifest(M(provides=frozenset({"routes"}), requires=frozenset({"data.activity"})))


def test_missing_manifest_is_refused_with_reason():
    code, text = code_of(check_manifest, None, module_id="x")
    assert code == "missing_manifest" and "манифест" in text


@pytest.mark.parametrize("bad", ["", "Alpha", "1abc", "a b", "a" * 40, "a-b"])
def test_bad_identifier_is_refused(bad):
    assert code_of(check_manifest, M(id=bad))[0] == "bad_id"


@pytest.mark.parametrize("version", ["", "1", "x.y", "1.2.3.4", "v1.0"])
def test_bad_module_version_is_refused(version):
    assert code_of(check_manifest, M(version=version))[0] == "bad_version"


def test_incompatible_contract_major_is_refused_with_both_versions_in_the_message():
    code, text = code_of(check_manifest, M(contract="2.0"))
    assert code == "contract_incompatible" and "2.0" in text and CONTRACT_VERSION in text


def test_module_written_for_newer_minor_than_core_is_refused():
    assert code_of(check_manifest, M(contract="1.9"))[0] == "contract_incompatible"


def test_module_written_for_older_minor_still_works(monkeypatch):
    monkeypatch.setattr("core.manifest.CONTRACT_VERSION", "1.3")

    assert contract_compatible("1.0", "1.3") is True
    assert contract_compatible("1.4", "1.3") is False
    assert contract_compatible("garbage") is False


def test_unknown_provides_and_unknown_request_are_refused():
    assert code_of(check_manifest, M(provides=frozenset({"teleport"})))[0] == "unknown_provides"
    code, text = code_of(check_manifest, M(requires=frozenset({"data.secrets"})))
    assert code == "unknown_request" and "data.secrets" in text


def test_module_cannot_depend_on_itself():
    assert code_of(check_manifest, M(depends_on=("alpha",)))[0] == "self_dependency"


# ---- набор модулей ----


def test_duplicate_identifier_is_refused():
    assert code_of(check_set, [M("a"), M("a")])[0] == "duplicate_id"


def test_missing_dependency_is_refused_and_named():
    code, text = code_of(check_set, [M("a", depends_on=("ghost",))])
    assert code == "missing_dependency" and "ghost" in text


def test_dependency_cycle_is_refused_and_shown():
    code, text = code_of(
        check_set,
        [M("a", depends_on=("b",)), M("b", depends_on=("c",)), M("c", depends_on=("a",))],
    )
    assert code == "dependency_cycle" and "→" in text


def test_load_order_puts_dependencies_first():
    order = load_order([M("c", depends_on=("b",)), M("b", depends_on=("a",)), M("a")])

    assert order == ["a", "b", "c"]


# ---- согласованность манифеста с кодом ----


class _Stub(modules.BackendModule):
    id = "stub"
    manifest = M("stub")


def test_manifest_must_not_declare_what_the_module_does_not_implement():
    class Liar(_Stub):
        manifest = M("stub", provides=frozenset({"routes", "rubrics"}))

        def rubrics(self):
            return []

    code, text = code_of(modules.validate_modules, [Liar()])
    assert code == "provides_not_implemented" and "routes" in text


def test_manifest_must_declare_what_the_module_implements():
    class Quiet(_Stub):
        def rubrics(self):
            return []

    assert code_of(modules.validate_modules, [Quiet()])[0] == "undeclared_capability"


def test_manifest_id_must_match_module_id():
    class Wrong(_Stub):
        manifest = M("other")

    assert code_of(modules.validate_modules, [Wrong()])[0] == "id_mismatch"


def test_module_without_manifest_cannot_be_loaded():
    class Bare(modules.BackendModule):
        id = "bare"

    assert code_of(modules.validate_modules, [Bare()])[0] == "missing_manifest"


# ---- три базовых модуля ----


def test_three_base_modules_declare_honest_manifests():
    loaded = modules.load_modules()

    assert [m.id for m in loaded] == ["languages", "ml", "knowledge", "srs", "mnemonic"]
    modules.validate_modules(loaded)  # не бросает
    for m in loaded:
        assert m.manifest.contract == CONTRACT_VERSION
        assert modules.actual_provides(m) == m.manifest.provides


# ---- жизненный цикл ----


@pytest.fixture(autouse=True)
def _clean_state():
    modules.reset_state_cache()
    yield
    modules.reset_state_cache()


def test_first_sync_installs_every_module_enabled(session):
    lines = modules.sync_module_state(session)

    assert {r.id: r.enabled for r in session.query(ModuleState)} == {
        "languages": True,
        "ml": True,
        "knowledge": True,
        "srs": True,
        "mnemonic": True,
    }
    assert any("установлен" in line for line in lines)


def test_sync_is_idempotent(session):
    modules.sync_module_state(session)

    assert modules.sync_module_state(session) == []


def test_changed_version_is_recorded_as_update(session):
    modules.sync_module_state(session)
    row = session.get(ModuleState, "ml")
    row.version = "0.9"
    session.flush()

    lines = modules.sync_module_state(session)

    assert any("ml: обновлён 0.9 → 1.0" in line for line in lines)
    assert session.get(ModuleState, "ml").previous_version == "0.9"


def test_module_removed_from_config_keeps_its_row(session):
    session.add(ModuleState(id="gone", version="1.0"))
    session.flush()

    lines = modules.sync_module_state(session)

    assert any("gone" in line and "данные сохранены" in line for line in lines)
    assert session.get(ModuleState, "gone").installed is False


def test_disabling_hides_rubrics_and_keeps_data(session):
    modules.sync_module_state(session)
    modules.sync_rubrics(session)
    count = session.query(Rubric).filter_by(module="ml").count()

    modules.set_enabled(session, "ml", False)

    assert modules.is_enabled("ml") is False
    assert [m.id for m in modules.enabled_modules()] == [
        "languages",
        "knowledge",
        "srs",
        "mnemonic",
    ]
    assert session.query(Rubric).filter_by(module="ml").count() == count  # данные целы
    assert "grade_code" not in modules.grade_job_modules()


def test_enabling_back_restores_behaviour(session):
    modules.sync_module_state(session)
    modules.set_enabled(session, "ml", False)

    modules.set_enabled(session, "ml", True)

    assert "grade_code" in modules.grade_job_modules()


def test_unknown_module_cannot_be_toggled(session):
    modules.sync_module_state(session)

    with pytest.raises(modules.LifecycleError) as e:
        modules.set_enabled(session, "nope", False)
    assert e.value.code == "unknown_module"


def test_cannot_disable_module_that_others_depend_on(session, monkeypatch):
    modules.sync_module_state(session)
    ml = next(m for m in modules.load_modules() if m.id == "ml")
    monkeypatch.setattr(ml, "manifest", M("ml", depends_on=("knowledge",)))

    with pytest.raises(modules.LifecycleError) as e:
        modules.set_enabled(session, "knowledge", False)
    assert e.value.code == "has_dependents" and "ml" in str(e.value)


def test_cannot_enable_module_whose_dependency_is_off(session, monkeypatch):
    modules.sync_module_state(session)
    ml = next(m for m in modules.load_modules() if m.id == "ml")
    monkeypatch.setattr(ml, "manifest", M("ml", depends_on=("knowledge",)))
    modules.set_enabled(session, "ml", False)
    modules.set_enabled(session, "knowledge", False)

    with pytest.raises(modules.LifecycleError) as e:
        modules.set_enabled(session, "ml", True)
    assert e.value.code == "dependency_disabled"


def test_uninstall_requires_confirmation_disabled_state_and_purge_support(session):
    modules.sync_module_state(session)

    def code(**kw):
        with pytest.raises(modules.LifecycleError) as e:
            modules.uninstall(session, "ml", **kw)
        return e.value.code

    assert code(confirm=False) == "confirmation_required"
    assert code(confirm=True) == "still_enabled"
    modules.set_enabled(session, "ml", False)
    assert code(confirm=True) == "purge_unsupported"  # базовые модули данных не удаляют


def test_uninstall_calls_the_module_purge_and_forgets_state(session, monkeypatch):
    modules.sync_module_state(session)
    ml = next(m for m in modules.load_modules() if m.id == "ml")
    purged = []
    monkeypatch.setattr(
        type(ml), "purge_data", lambda self, s: purged.append(self.id), raising=False
    )
    modules.set_enabled(session, "ml", False)

    modules.uninstall(session, "ml", confirm=True)

    assert purged == ["ml"] and session.get(ModuleState, "ml") is None


# ---- маршруты и API ----


def test_disabled_module_routes_answer_503_not_500(session, client):
    user = make_user(session)
    api = client(user)
    modules.sync_module_state(session)
    assert api.get("/graph/d").status_code == 200

    modules.set_enabled(session, "knowledge", False)
    r = api.get("/graph/d")

    assert r.status_code == 503 and r.json()["detail"]["code"] == "module_disabled"
    modules.set_enabled(session, "knowledge", True)
    assert api.get("/graph/d").status_code == 200


def test_module_list_shows_manifests_and_state(session, client):
    modules.sync_module_state(session)

    rows = client(make_user(session, superuser=True)).get("/v1/modules").json()

    ml = next(r for r in rows if r["id"] == "ml")
    assert ml["enabled"] is True and ml["contract"] == CONTRACT_VERSION
    assert "apply_activity" in ml["provides"] and ml["version"] == "1.0"


def test_lifecycle_api_is_admin_only(session, client):
    modules.sync_module_state(session)
    user_api = client(make_user(session))

    assert user_api.get("/v1/modules").status_code == 403
    assert user_api.post("/v1/modules/ml/disable").status_code == 403


def test_disable_and_enable_via_api_with_readable_errors(session, client):
    modules.sync_module_state(session)
    api = client(make_user(session, superuser=True))

    assert api.post("/v1/modules/ml/disable").json() == {"id": "ml", "enabled": False}
    assert api.post("/v1/modules/ml/enable").json() == {"id": "ml", "enabled": True}
    missing = api.post("/v1/modules/nope/disable")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "unknown_module"
    unsafe = api.post("/v1/modules/ml/uninstall", json={})
    assert unsafe.status_code == 400 and unsafe.json()["detail"]["code"] == "confirmation_required"


def test_stub_router_is_not_required_for_manifest_checks():
    assert _Stub().router() is None and isinstance(APIRouter(), APIRouter)
