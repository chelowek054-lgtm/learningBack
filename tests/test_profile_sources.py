"""Автопоиск и разбор нескольких источников для новых областей профиля (T-0097)."""

from __future__ import annotations

import pytest

from core import objects
from core.config import settings
from core.jobs import process_job
from core.models import Job
from core.objects import MemoryObjectStore
from modules.knowledge import (
    goal_intake,
    profile_sources,
    profile_store,
    skill_profile,
    source_search,
)
from modules.knowledge.models import SourceDocument
from modules.knowledge.source_search import Candidate
from tests.conftest import make_user

WIKI = (
    "==Section==\nA longer paragraph of source text that talks about the area in enough words to be "
    "kept as a fragment of the document and later parsed into concepts by the model.\n"
)


def cand(n, provider):
    return Candidate(
        f"Doc {n}",
        f"https://en.wikibooks.org/w/index.php?title=D{n}&action=raw",
        provider,
        "CC BY-SA",
        "wikitext",
    )


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    store = MemoryObjectStore()
    monkeypatch.setattr(objects, "_store", store)
    yield store
    objects.reset_store()


@pytest.fixture
def fetched(monkeypatch):
    monkeypatch.setattr(
        source_search, "fetch", lambda c, t=None: ("doc.txt", WIKI.encode() + c.url.encode())
    )


class Finder:
    name = "f"

    def __init__(self, found):
        self.found, self.queries = found, []

    def search(self, query, limit):
        self.queries.append(query)
        return self.found


def test_queries_are_several_and_distinct():
    qs = profile_sources.queries_for(
        "Область", ["Основы", "Методы", "Практика"], ["Понятие", "Другое", "Третье"]
    )
    assert qs[0] == "Область" and len(qs) == 3 and len(set(qs)) == 3
    assert profile_sources.queries_for("  ", [], []) == []
    assert profile_sources.queries_for("Область", [], []) == ["Область"]


def test_sources_come_from_different_providers_in_turn():
    cands = [
        cand(1, "wikibooks"),
        cand(2, "wikibooks"),
        cand(3, "wikibooks"),
        cand(4, "arxiv"),
        cand(5, "web"),
    ]
    picked = profile_sources.pick_diverse(cands, 3)
    assert [c.provider for c in picked] == ["wikibooks", "arxiv", "web"]
    assert len(profile_sources.pick_diverse(cands[:2], 3)) == 2  # меньше не бывает — берём что есть


def test_job_searches_several_queries_and_queues_ingestion(session, monkeypatch, fetched):
    finder_a, finder_b = (
        Finder([cand(1, "wikibooks"), cand(2, "wikibooks")]),
        Finder([cand(3, "arxiv")]),
    )
    monkeypatch.setattr(source_search, "default_finders", lambda: [finder_a, finder_b])
    user = make_user(session)
    job = profile_sources.enqueue(session, user.id, "Область", ["запрос раз", "запрос два"])

    process_job(session, job, None)

    assert job.status == "done" and job.result["status"] == "queued"
    assert job.result["providers"] == ["arxiv", "wikibooks"] and job.result["queued"] == 3
    assert finder_a.queries == ["запрос раз", "запрос два"]
    docs = session.query(SourceDocument).filter_by(domain="Область").all()
    assert len(docs) == 3
    assert session.query(Job).filter_by(type="ingest_document").count() == 3  # разбор поставлен


def test_nothing_found_is_a_normal_result(session, monkeypatch):
    monkeypatch.setattr(source_search, "default_finders", lambda: [Finder([])])
    job = profile_sources.enqueue(session, make_user(session).id, "Область", ["запрос"])
    process_job(session, job, None)
    assert job.status == "done" and job.result["status"] == "nothing_found"


def test_dead_catalog_does_not_fail_the_job(session, monkeypatch, fetched):
    class Dead(Finder):
        def search(self, query, limit):
            raise ConnectionError("каталог недоступен")

    monkeypatch.setattr(
        source_search, "default_finders", lambda: [Dead([]), Finder([cand(1, "web")])]
    )
    job = profile_sources.enqueue(session, make_user(session).id, "Область", ["запрос"])
    process_job(session, job, None)
    assert job.status == "done" and job.result["queued"] == 1


def test_second_search_while_the_first_runs_is_not_queued(session):
    user = make_user(session)
    assert profile_sources.enqueue(session, user.id, "Область", ["запрос"]) is not None
    assert profile_sources.enqueue(session, user.id, "Область", ["запрос"]) is None
    assert profile_sources.enqueue(session, user.id, "Другая", ["запрос"]) is not None


def test_switched_off_or_no_queries_means_no_job(session, monkeypatch):
    user = make_user(session)
    assert profile_sources.enqueue(session, user.id, "Область", []) is None
    monkeypatch.setattr(settings, "profile_auto_sources", False)
    assert profile_sources.enqueue(session, user.id, "Область", ["запрос"]) is None


def test_job_without_a_domain_fails_for_good(session):
    user = make_user(session)
    job = Job(user_id=user.id, type=profile_sources.JOB_TYPE, status="pending", input_ref={})
    session.add(job)
    session.flush()
    process_job(session, job, None)
    assert job.status == "failed"


def test_building_the_skeleton_queues_a_search_for_each_new_area(session, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    user = make_user(session)
    goal_intake.confirm(session, user.id, "ml", {"area": "Навык", "goal": "цель", "level": "apply"})

    report = profile_store.build_from_goal(session, user.id, "ml")

    jobs = session.query(Job).filter_by(type=profile_sources.JOB_TYPE).all()
    assert (
        {j.input_ref["domain"] for j in jobs}
        == set(report["sources"])
        == {"Навык: предпосылки", "ml"}
    )
    assert all(j.input_ref["queries"] for j in jobs)


def test_rebuilding_does_not_search_again(session, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    user = make_user(session)
    goal_intake.confirm(session, user.id, "ml", {"area": "Навык", "goal": "цель", "level": "apply"})
    profile_store.build_from_goal(session, user.id, "ml")
    row = profile_store.get(session, user.id, "ml")

    again = profile_store.build_graph(session, row)  # понятия уже есть — создавать нечего

    assert again["sources"] == []
