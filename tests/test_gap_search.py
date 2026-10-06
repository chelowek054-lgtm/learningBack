# ruff: noqa: F811  (фикстура world импортирована из test_chain_placement)
"""Поиск источников по областям «нет в графе» (T-0084)."""

from __future__ import annotations

import httpx
import pytest

from core import objects
from core.objects import MemoryObjectStore
from modules.knowledge import domains, gap_search, ingest
from modules.knowledge.models import SourceDocument
from modules.knowledge.source_search import Candidate
from tests.conftest import make_user
from tests.test_chain_placement import world  # noqa: F401

WIKI_BODY = (
    "==Deep things==\nA ''deep thing'' is an object that can accept arguments and return values "
    "when it is called by other things in a program.\n"
)


class FakeFinder:
    name = "fake"

    def __init__(self, found):
        self.found = found
        self.queries: list[str] = []

    def search(self, query, limit):
        self.queries.append(query)
        return self.found


def cand(n=1):
    return Candidate(
        f"Deep {n} (Wikibooks)",
        f"https://en.wikibooks.org/w/index.php?title=Deep{n}&action=raw",
        "wikibooks",
        "CC BY-SA",
        "wikitext",
    )


def net():
    return httpx.MockTransport(lambda r: httpx.Response(200, text=WIKI_BODY))


@pytest.fixture(autouse=True)
def _memory_store(monkeypatch):
    store = MemoryObjectStore()
    monkeypatch.setattr(objects, "_store", store)
    yield store
    objects.reset_store()


@pytest.fixture
def gap(session, world):
    """В мире test_chain_placement добавлена опорная область «deep» без понятий под «mid»."""
    domains.register(session, "deep", foundation=True)
    domains.add_prereq(session, "mid", "deep")
    return make_user(session, superuser=True)


def test_gap_is_listed_with_a_ready_query(session, gap):
    out = gap_search.gaps(session, gap.id, "top", "understand")

    assert [g["key"] for g in out["gaps"]] == ["deep"]
    g = out["gaps"][0]
    assert g["query"] == "deep" and g["state"] == "idle" and g["documents"] == []


def test_areas_present_in_the_graph_are_not_gaps(session, world):
    assert gap_search.gaps(session, make_user(session).id, "top", "understand")["gaps"] == []


def test_fill_searches_the_whitelist_and_queues_ingestion(session, gap):
    finder = FakeFinder([cand()])

    res = gap_search.fill(
        session,
        gap.id,
        "top",
        "understand",
        [{"area": "deep", "query": ""}],
        finders=[finder],
        transport=net(),
    )

    assert finder.queries == ["deep"]
    assert res[0]["status"] == "queued" and len(res[0]["queued"]) == 1
    doc = session.get(SourceDocument, res[0]["queued"][0]["documentId"])
    assert doc.domain == "deep" and doc.origin_url == cand().url


def test_custom_query_replaces_the_default(session, gap):
    finder = FakeFinder([])

    res = gap_search.fill(
        session,
        gap.id,
        "top",
        "understand",
        [{"area": "deep", "query": "deep calculus book"}],
        finders=[finder],
    )

    assert finder.queries == ["deep calculus book"] and res[0]["status"] == "nothing_found"


def test_second_launch_while_ingestion_runs_is_refused(session, gap):
    gap_search.fill(
        session,
        gap.id,
        "top",
        "understand",
        [{"area": "deep"}],
        finders=[FakeFinder([cand()])],
        transport=net(),
    )
    state = gap_search.gaps(session, gap.id, "top", "understand")["gaps"][0]
    assert state["state"] == "running" and state["documents"][0]["status"] == "queued"

    finder = FakeFinder([cand(2)])
    again = gap_search.fill(
        session, gap.id, "top", "understand", [{"area": "deep"}], finders=[finder], transport=net()
    )

    assert again[0]["status"] == "already_running" and finder.queries == []


def test_finished_document_allows_a_new_search(session, gap):
    res = gap_search.fill(
        session,
        gap.id,
        "top",
        "understand",
        [{"area": "deep"}],
        finders=[FakeFinder([cand()])],
        transport=net(),
    )
    doc = session.get(SourceDocument, res[0]["queued"][0]["documentId"])
    doc.meta = {**doc.meta, "ingest": {"status": "done"}}
    session.flush()
    from core.models import Job

    for job in session.query(Job).filter_by(type=ingest.JOB_TYPE):
        job.status = "done"
    session.flush()

    state = gap_search.gaps(session, gap.id, "top", "understand")["gaps"][0]
    assert state["state"] == "idle"


def test_unknown_or_filled_area_is_not_searched(session, gap):
    with pytest.raises(gap_search.GapError) as e:
        gap_search.fill(session, gap.id, "top", "understand", [{"area": "mid"}], finders=[])
    assert e.value.code == "not_a_gap"
    with pytest.raises(gap_search.GapError):
        gap_search.fill(session, gap.id, "top", "understand", [], finders=[])


def test_too_short_query_is_reported_not_raised(session, gap):
    res = gap_search.fill(
        session,
        gap.id,
        "top",
        "understand",
        [{"area": "deep", "query": "x"}],
        finders=[FakeFinder([])],
    )
    # пустой запрос подменяется готовым, поэтому «x» — единственный способ получить короткий
    assert res[0]["status"] == "bad_query"


# ---- API ----


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def get(self, url, **kw):
        return self._c(self._u).get(url, **kw)

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)


def test_endpoints_are_admin_only(session, client, gap):
    learner = As(client, make_user(session))
    admin = As(client, gap)
    body = {"target": "understand", "areas": [{"area": "deep"}]}

    assert learner.get("/graph/sources/gaps/top").status_code == 403
    assert learner.post("/graph/sources/gaps/top/fill", json=body).status_code == 403

    got = admin.get("/graph/sources/gaps/top").json()
    assert [g["key"] for g in got["gaps"]] == ["deep"]


def test_fill_endpoint_rejects_a_foreign_area(session, client, gap):
    admin = As(client, gap)
    r = admin.post("/graph/sources/gaps/top/fill", json={"areas": [{"area": "mid"}]})
    assert r.status_code == 422 and "не отсутствует" in r.json()["detail"]
