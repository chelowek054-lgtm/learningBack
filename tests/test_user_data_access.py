"""Данные человека: доступ модулей по разрешениям, журнал, выгрузка и удаление (T-0054, R-0031, V-0083)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from core import modules, userdata
from core.manifest import ModuleManifest
from core.models import Activity, DataAccessLog, Response, SrsCard
from core.userdata import DataAccessError, DataType
from modules.knowledge.models import Course
from tests.conftest import make_user


class ThirdParty(modules.BackendModule):
    """Сторонний модуль: просит карточки, больше ничего."""

    id = "thirdparty"
    manifest = ModuleManifest(
        "thirdparty", "Сторонний", "1.0", requires=frozenset({"data.srs_card"})
    )


class Greedy(modules.BackendModule):
    """Просит только карточки, а пытается достать ответы."""

    id = "greedy"
    manifest = ModuleManifest("greedy", "Жадный", "1.0", requires=frozenset({"data.srs_card"}))


@pytest.fixture(autouse=True)
def _clean_state():
    modules.reset_state_cache()
    yield
    modules.reset_state_cache()


def card(session, user):
    c = SrsCard(
        user_id=user.id,
        module="x",
        front={"q": 1},
        back={"a": 1},
        source="generated",
        fsrs_state={},
        due_at=datetime.now(timezone.utc),
    )
    session.add(c)
    session.flush()
    return c


def registry(*extra):
    return userdata.types([*modules.load_modules(), *extra])


# ---- реестр типов ----


def test_every_type_has_owner_purpose_and_retention(session):
    reg = registry()

    assert {"profile", "activity", "response", "srs_card", "job", "material"} <= set(reg)
    assert {"mastery", "course", "goal"} <= set(reg)  # типы граф-модуля
    for t in reg.values():
        d = t.describe()
        assert d["owner"] and d["purpose"] and d["title"] and "retentionDays" in d


def test_duplicate_type_from_a_module_is_refused():
    class Dup(modules.BackendModule):
        id = "dup"
        manifest = ModuleManifest("dup", "Dup", "1.0")

        def data_types(self):
            return [userdata.core_types()[1]]  # тот же activity

    with pytest.raises(ValueError):
        userdata.types([Dup()])


# ---- доступ только по разрешениям ----


def test_module_without_declaration_gets_a_refusal_and_a_log_entry(session):
    user = make_user(session)
    card(session, user)

    with pytest.raises(DataAccessError) as e:
        userdata.read(session, Greedy(), user.id, "response", "тест", registry())

    assert e.value.code == "not_declared"
    log = userdata.access_log(session, user.id)
    assert (
        log[0]["allowed"] is False
        and log[0]["type"] == "response"
        and log[0]["reason"] == "not_declared"
    )


def test_third_party_module_has_no_access_until_the_person_allows(session):
    user = make_user(session)
    card(session, user)
    mod = ThirdParty()

    with pytest.raises(DataAccessError) as e:
        userdata.read(session, mod, user.id, "srs_card", "тест", registry())
    assert e.value.code == "not_granted"

    userdata.set_permission(session, user.id, mod, "srs_card", "read", True)

    assert len(userdata.read(session, mod, user.id, "srs_card", "повторение", registry())) == 1


def test_person_can_revoke_and_access_stops(session):
    user = make_user(session)
    mod = ThirdParty()
    userdata.set_permission(session, user.id, mod, "srs_card", "read", True)
    userdata.read(session, mod, user.id, "srs_card", "тест", registry())

    userdata.set_permission(session, user.id, mod, "srs_card", "read", False)

    with pytest.raises(DataAccessError) as e:
        userdata.read(session, mod, user.id, "srs_card", "тест", registry())
    assert e.value.code == "revoked"


def test_read_and_write_are_allowed_separately(session):
    user = make_user(session)
    mod = ThirdParty()
    userdata.set_permission(session, user.id, mod, "srs_card", "read", True)

    with pytest.raises(DataAccessError) as e:
        userdata.write(session, mod, user.id, "srs_card", "тест", [], registry())
    assert e.value.code == "not_granted"


def test_write_goes_through_the_type_and_needs_a_writer(session):
    user = make_user(session)
    mod = ThirdParty()
    userdata.set_permission(session, user.id, mod, "srs_card", "write", True)

    with pytest.raises(DataAccessError) as e:  # у типа нет записи — только чтение
        userdata.write(session, mod, user.id, "srs_card", "тест", [{}], registry())
    assert e.value.code == "read_only"

    written = []
    reg = registry()
    reg["srs_card"] = DataType(
        "srs_card",
        "Карточки",
        "core",
        "Повторение",
        None,
        lambda s, u: [],
        lambda s, u: 0,
        write=lambda s, u, rec: written.append((u, rec)) or len(rec),
    )
    assert userdata.write(session, mod, user.id, "srs_card", "тест", [{"a": 1}], reg) == 1
    assert written == [(user.id, [{"a": 1}])]


def test_first_party_module_is_allowed_by_default_and_can_be_revoked(session):
    user = make_user(session)
    srs = next(m for m in modules.load_modules() if m.id == "srs")
    card(session, user)

    assert len(userdata.read(session, srs, user.id, "srs_card", "повторение", registry())) == 1

    userdata.set_permission(session, user.id, srs, "srs_card", "read", False)
    with pytest.raises(DataAccessError):
        userdata.read(session, srs, user.id, "srs_card", "повторение", registry())


def test_permission_cannot_be_given_for_an_undeclared_type(session):
    with pytest.raises(DataAccessError) as e:
        userdata.set_permission(session, make_user(session).id, Greedy(), "response", "read", True)
    assert e.value.code == "not_declared"


def test_module_sees_only_the_data_of_the_person_it_was_asked_about(session):
    mine, other = make_user(session), make_user(session)
    card(session, mine)
    card(session, other)
    card(session, other)
    mod = ThirdParty()
    userdata.set_permission(session, mine.id, mod, "srs_card", "read", True)

    rows = userdata.read(session, mod, mine.id, "srs_card", "тест", registry())

    assert len(rows) == 1 and rows[0]["user_id"] == str(mine.id)


def test_unknown_type_is_refused(session):
    with pytest.raises(DataAccessError) as e:
        userdata.read(session, ThirdParty(), make_user(session).id, "nothing", "тест", registry())
    assert e.value.code == "unknown_type"


# ---- журнал ----


def test_log_records_who_what_why_and_the_outcome(session):
    user = make_user(session)
    mod = ThirdParty()
    userdata.set_permission(session, user.id, mod, "srs_card", "read", True)
    userdata.read(session, mod, user.id, "srs_card", "повторение карточек", registry())

    entry = userdata.access_log(session, user.id)[0]

    assert (entry["module"], entry["type"], entry["mode"], entry["purpose"], entry["allowed"]) == (
        "thirdparty",
        "srs_card",
        "read",
        "повторение карточек",
        True,
    )
    assert entry["at"]


def test_log_of_one_person_does_not_show_in_another(session):
    a, b = make_user(session), make_user(session)
    with pytest.raises(DataAccessError):
        userdata.read(session, Greedy(), a.id, "response", "тест", registry())

    assert userdata.access_log(session, b.id) == []


# ---- выгрузка и удаление ----


def seed(session, user):
    act = Activity(user_id=user.id, module="x", type="t", connectivity="online", payload={})
    session.add(act)
    session.flush()
    session.add(
        Response(
            activity_id=act.id,
            user_id=user.id,
            user_answer={"a": 1},
            local_created_at=datetime.now(timezone.utc),
        )
    )
    session.add(
        Course(user_id=user.id, domain="d", target={"bloom": "apply"}, path=[], progress={})
    )
    card(session, user)
    user.profile = {"subject": {"id": "d"}}
    session.flush()


def test_export_contains_every_type_of_the_person_and_nothing_of_others(session):
    me, other = make_user(session), make_user(session)
    seed(session, me)
    seed(session, other)

    out = userdata.export_all(session, me.id, registry())

    assert out["userId"] == str(me.id)
    data = out["data"]
    assert (
        len(data["activity"])
        == len(data["response"])
        == len(data["srs_card"])
        == len(data["course"])
        == 1
    )
    assert data["profile"][0]["profile"]["subject"]["id"] == "d"
    assert all(
        r["user_id"] == str(me.id)
        for t in ("activity", "response", "srs_card", "course")
        for r in data[t]
    )


def test_erase_removes_all_of_the_person_including_permissions_and_log(session):
    me, other = make_user(session), make_user(session)
    seed(session, me)
    seed(session, other)
    mod = ThirdParty()
    userdata.set_permission(session, me.id, mod, "srs_card", "read", True)
    userdata.read(session, mod, me.id, "srs_card", "тест", registry())

    counts = userdata.erase_all(session, me.id, registry())

    assert counts["response"] == counts["activity"] == counts["srs_card"] == counts["course"] == 1
    after = userdata.export_all(session, me.id, registry())["data"]
    assert all(not v or v == [] for k, v in after.items() if k != "profile") and after[
        "profile"
    ] == [{"profile": {}}]
    assert session.query(DataAccessLog).filter_by(user_id=me.id).count() == 0
    # чужое не тронуто
    assert len(userdata.export_all(session, other.id, registry())["data"]["srs_card"]) == 1


# ---- API ----


def test_api_lists_permissions_and_the_person_can_revoke(session, client):
    user = make_user(session)
    api = client(user)

    perms = api.get("/me/data/permissions").json()
    srs = next(p for p in perms if p["module"] == "srs")
    assert srs["firstParty"] is True and srs["data"] == [
        {"type": "srs_card", "read": True, "write": True}
    ]

    r = api.put(
        "/me/data/permissions",
        json={"module": "srs", "type": "srs_card", "mode": "read", "granted": False},
    )
    assert r.status_code == 200
    srs = next(p for p in api.get("/me/data/permissions").json() if p["module"] == "srs")
    assert srs["data"][0]["read"] is False and srs["data"][0]["write"] is True


def test_api_refuses_permission_for_undeclared_type_and_unknown_module(session, client):
    api = client(make_user(session))

    bad_type = api.put(
        "/me/data/permissions",
        json={"module": "srs", "type": "response", "mode": "read", "granted": True},
    )
    no_module = api.put(
        "/me/data/permissions",
        json={"module": "ghost", "type": "x", "mode": "read", "granted": True},
    )

    assert bad_type.status_code == 422 and no_module.status_code == 404


def test_api_types_export_log_and_erase(session, client):
    user = make_user(session)
    seed(session, user)
    api = client(user)

    types = {t["id"]: t for t in api.get("/me/data/types").json()}
    export = api.get("/me/data/export").json()
    refused = api.post("/me/data/erase", json={})
    erased = api.post("/me/data/erase", json={"confirm": True})

    assert types["response"]["owner"] == "core" and types["mastery"]["owner"] == "knowledge"
    assert len(export["data"]["srs_card"]) == 1
    assert refused.status_code == 422
    assert erased.status_code == 200 and erased.json()["erased"]["srs_card"] == 1
    assert api.get("/me/data/export").json()["data"]["srs_card"] == []
    assert api.get("/me/data/access-log").json() == []


def test_new_user_data_type_comes_from_a_module_without_touching_the_core(session):
    class Notes(modules.BackendModule):
        id = "notes"
        manifest = ModuleManifest("notes", "Заметки", "1.0")
        store: dict = {}

        def data_types(self):
            return [
                DataType(
                    "note",
                    "Заметки",
                    "notes",
                    "Личные заметки",
                    365,
                    lambda s, u: list(self.store.get(u, [])),
                    lambda s, u: len(self.store.pop(u, [])),
                )
            ]

    mod = Notes()
    user = make_user(session)
    mod.store[user.id] = [{"t": "x"}]
    reg = userdata.types([*modules.load_modules(), mod])

    assert reg["note"].retention_days == 365
    assert userdata.export_all(session, user.id, reg)["data"]["note"] == [{"t": "x"}]
    assert userdata.erase_all(session, user.id, reg)["note"] == 1
    assert uuid.UUID(str(user.id))
