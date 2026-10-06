"""Поиск источников: белый список, лимиты, происхождение (T-0082, V-0099)."""

from __future__ import annotations

import json

import httpx
import pytest

from core import objects
from core.config import settings
from core.models import Job
from core.objects import MemoryObjectStore
from modules.knowledge import provenance, source_search
from modules.knowledge.models import SourceDocument
from modules.knowledge.source_search import (
    ArxivFinder,
    BraveFinder,
    Candidate,
    SearchError,
    WikibooksFinder,
)
from tests.conftest import make_user

WIKI_BODY = (
    "{{Navigation|a|b}}\n==Function Calls==\nA ''callable object'' is an object that can accept "
    "[[Python Programming/Classes|classes]] and arguments.\n<pre>\ndef f(x):\n    return x\n</pre>\n"
)

ARXIV_FEED = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Group  theory
      lecture notes</title>
    <link href="https://arxiv.org/abs/1.1v1" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/1.1v1" rel="related" type="application/pdf" title="pdf"/>
    <summary>Notes on groups.</summary>
  </entry>
  <entry><title>No pdf link</title></entry>
</feed>"""


def transport(routes):
    """Заглушка сети: (подстрока адреса → ответ). Неизвестный адрес — 404, чтобы тест не ходил наружу."""

    def handler(request: httpx.Request) -> httpx.Response:
        for needle, response in routes.items():
            if needle in str(request.url):
                return response(request) if callable(response) else response
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _memory_store(monkeypatch):
    store = MemoryObjectStore()
    monkeypatch.setattr(objects, "_store", store)
    yield store
    objects.reset_store()


# ---- белый список ----


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://en.wikibooks.org/w/index.php?title=X", True),
        ("https://wikibooks.org/x", True),
        ("https://arxiv.org/pdf/1.pdf", True),
        ("https://evil.example.com/book.pdf", False),
        ("https://wikibooks.org.evil.com/x", False),  # домен-двойник
        ("https://notwikibooks.org/x", False),
        ("ftp://wikibooks.org/x", False),
        ("javascript:alert(1)", False),
        ("not a url", False),
    ],
)
def test_only_whitelisted_domains_and_their_subdomains_pass(url, ok):
    assert source_search.host_allowed(url) is ok


def test_admin_can_widen_the_whitelist(monkeypatch):
    assert not source_search.host_allowed("https://docs.example.org/guide.pdf")
    monkeypatch.setattr(settings, "source_domains", "example.org")
    assert source_search.host_allowed("https://docs.example.org/guide.pdf")


# ---- поставщики ----


def test_wikibooks_finder_parses_hits_and_skips_stubs():
    payload = {
        "query": {
            "search": [
                {
                    "title": "Python Programming/Functions",
                    "size": 13658,
                    "snippet": "<span>functions</span> in Python",
                },
                {"title": "Tiny stub", "size": 200, "snippet": "x"},
            ]
        }
    }
    finder = WikibooksFinder(
        transport(({"wikibooks.org/w/api.php": httpx.Response(200, json=payload)})),
        hosts=("en.wikibooks.org",),
    )
    found = finder.search("python functions", 5)
    assert [c.title for c in found] == ["Python Programming/Functions (Wikibooks)"]
    assert found[0].license == "CC BY-SA" and found[0].kind == "wikitext"
    assert found[0].summary == "functions in Python"
    assert "action=raw" in found[0].url and "Python_Programming/Functions" in found[0].url


def test_arxiv_finder_parses_atom_and_needs_a_pdf_link():
    finder = ArxivFinder(transport({"export.arxiv.org": httpx.Response(200, text=ARXIV_FEED)}))
    found = finder.search("group theory", 5)
    assert len(found) == 1
    assert found[0].title == "Group theory lecture notes (arXiv)"
    assert found[0].url == "https://arxiv.org/pdf/1.1v1.pdf" and found[0].kind == "pdf"


def test_brave_finder_keeps_only_whitelisted_results(monkeypatch):
    monkeypatch.setattr(settings, "brave_api_key", "k")
    seen = {}

    def answer(request):
        seen["q"] = request.url.params["q"]
        seen["token"] = request.headers["X-Subscription-Token"]
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Good",
                            "url": "https://openstax.org/books/algebra.pdf",
                            "description": "d",
                        },
                        {"title": "Bad", "url": "https://evil.example.com/a.pdf"},
                    ]
                }
            },
        )

    found = BraveFinder(transport({"api.search.brave.com": answer})).search("algebra", 5)
    assert [c.title for c in found] == ["Good"] and found[0].kind == "pdf"
    assert "site:openstax.org" in seen["q"] and seen["token"] == "k"


def test_search_collects_providers_dedupes_and_survives_a_dead_one():
    class Good:
        name = "good"

        def search(self, q, limit):
            c = Candidate("A", "https://arxiv.org/pdf/1.pdf", "good", "x", "pdf")
            return [c, c]  # повтор адреса

    class Dead:
        name = "dead"

        def search(self, q, limit):
            raise httpx.ConnectError("нет сети")

    class Foreign:
        name = "foreign"

        def search(self, q, limit):
            return [Candidate("B", "https://evil.example.com/x.pdf", "foreign", "x", "pdf")]

    found, problems = source_search.search("group theory", 5, finders=[Dead(), Good(), Foreign()])
    assert [c.url for c in found] == [
        "https://arxiv.org/pdf/1.pdf"
    ]  # чужой домен отсеян даже у поставщика
    assert len(problems) == 1 and problems[0].startswith("dead:")
    with pytest.raises(SearchError):
        source_search.search("ab", 5, finders=[])


# ---- скачивание ----


def test_wikitext_becomes_markdown_with_chapters():
    md = source_search.wikitext_to_markdown(WIKI_BODY)
    assert md.startswith("# Function Calls")
    assert "{{" not in md and "[[" not in md and "''" not in md
    assert "classes and arguments" in md and "```\ndef f(x):" in md


def cand(
    url="https://en.wikibooks.org/w/index.php?title=A&action=raw",
    kind="wikitext",
    title="A (Wikibooks)",
):
    return Candidate(title, url, "wikibooks", "CC BY-SA", kind)


def test_fetch_refuses_a_foreign_domain_without_any_request():
    calls = []
    t = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, text="x"))
    with pytest.raises(SearchError) as e:
        source_search.fetch(cand(url="https://evil.example.com/a.pdf", kind="pdf"), t)
    assert e.value.code == "domain_not_allowed" and calls == []


def test_redirect_to_a_foreign_domain_is_refused_but_to_an_allowed_one_followed():
    bad = transport(
        {"wikibooks.org": httpx.Response(302, headers={"location": "https://evil.example.com/x"})}
    )
    with pytest.raises(SearchError) as e:
        source_search.fetch(cand(), bad)
    assert e.value.code == "domain_not_allowed"

    def hop(request):
        if "wikibooks.org" in request.url.host:
            return httpx.Response(302, headers={"location": "https://en.wikibooks.org/final"})
        return httpx.Response(404)

    routes = {"final": httpx.Response(200, text=WIKI_BODY), "w/index.php": hop}
    name, data = source_search.fetch(cand(), transport(routes))
    assert name.endswith(".md") and data.decode().startswith("# Function Calls")


def test_too_large_file_is_refused_by_header_and_by_stream(monkeypatch):
    monkeypatch.setattr(settings, "source_fetch_max_bytes", 100)
    declared = transport(
        {
            "wikibooks.org": httpx.Response(
                200, headers={"content-length": "5000"}, content=b"x" * 50
            )
        }
    )
    with pytest.raises(SearchError) as e:
        source_search.fetch(cand(), declared)
    assert e.value.code == "too_large"
    streamed = transport({"wikibooks.org": httpx.Response(200, content=b"x" * 500)})
    with pytest.raises(SearchError):
        source_search.fetch(cand(), streamed)


def test_html_pages_are_not_ingested_yet():
    t = transport({"openstax.org": httpx.Response(200, text="<html></html>")})
    with pytest.raises(SearchError) as e:
        source_search.fetch(cand(url="https://openstax.org/page", kind="html"), t)
    assert e.value.code == "unsupported_kind"


# ---- в граф ----


def test_fetched_document_keeps_its_origin_and_queues_ingestion(session):
    user = make_user(session)
    t = transport({"wikibooks.org": httpx.Response(200, text=WIKI_BODY)})

    result = source_search.fetch_and_queue(session, user.id, "python", [cand()], transport=t)

    assert len(result["queued"]) == 1 and result["skipped"] == []
    doc = session.get(SourceDocument, result["queued"][0]["documentId"])
    assert doc.origin_url == cand().url and doc.license == "CC BY-SA" and doc.domain == "python"
    assert doc.meta["provider"] == "wikibooks" and doc.object_key
    job = session.query(Job).filter_by(type="ingest_document").one()
    assert job.input_ref == {"documentId": str(doc.id)}


def test_known_url_is_skipped_and_budget_is_respected(session, monkeypatch):
    user = make_user(session)
    t = transport({"wikibooks.org": lambda r: httpx.Response(200, text=WIKI_BODY + str(r.url))})
    urls = [f"https://en.wikibooks.org/w/index.php?title=P{i}&action=raw" for i in range(4)]
    cands = [cand(url=u, title=f"P{i}") for i, u in enumerate(urls)]
    monkeypatch.setattr(settings, "source_max_docs_per_request", 2)

    first = source_search.fetch_and_queue(
        session, make_user(session).id, "python", cands, transport=t
    )
    assert len(first["queued"]) == 2
    assert {s["reason"] for s in first["skipped"]} == {"превышен лимит документов на запрос"}

    again = source_search.fetch_and_queue(session, user.id, "python", cands[:2], transport=t)
    assert again["queued"] == [] and {s["reason"] for s in again["skipped"]} == {"уже загружен"}


def test_one_unreadable_document_does_not_stop_the_others(session):
    user = make_user(session)
    t = transport(
        {
            "title=Bad": httpx.Response(200, content=b"%PDF-1.4 not really a pdf"),
            "title=Good": httpx.Response(200, text=WIKI_BODY),
        }
    )
    bad = cand(
        url="https://en.wikibooks.org/w/index.php?title=Bad&action=raw", kind="pdf", title="Bad"
    )
    good = cand(url="https://en.wikibooks.org/w/index.php?title=Good&action=raw", title="Good")

    result = source_search.fetch_and_queue(session, user.id, "python", [bad, good], transport=t)

    assert [q["title"] for q in result["queued"]] == ["Good"]
    assert len(result["skipped"]) == 1 and result["skipped"][0]["url"] == bad.url


def test_removing_a_found_source_removes_the_document(session):
    user = make_user(session)
    t = transport({"wikibooks.org": httpx.Response(200, text=WIKI_BODY)})
    result = source_search.fetch_and_queue(session, user.id, "python", [cand()], transport=t)
    doc = session.get(SourceDocument, result["queued"][0]["documentId"])

    provenance.remove_document(session, doc)
    session.flush()

    assert session.query(SourceDocument).count() == 0


# ---- API ----


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)


def test_search_and_fetch_endpoints_are_admin_only_and_check_the_whitelist(
    session, client, monkeypatch
):
    learner = As(client, make_user(session))
    admin = As(client, make_user(session, superuser=True))
    body = {"query": "python functions"}
    assert learner.post("/graph/sources/search", json=body).status_code == 403
    one = {"title": "A", "url": "https://evil.example.com/a.pdf", "kind": "pdf"}
    assert (
        learner.post("/graph/sources/fetch", json={"domain": "d", "candidates": [one]}).status_code
        == 403
    )

    refused = admin.post("/graph/sources/fetch", json={"domain": "d", "candidates": [one]})
    assert refused.status_code == 422 and "белого списка" in refused.json()["detail"]
    assert admin.post("/graph/sources/search", json={"query": "ab"}).status_code == 422

    monkeypatch.setattr(source_search, "default_finders", lambda: [])
    ok = admin.post("/graph/sources/search", json=body).json()
    assert ok["candidates"] == [] and "wikibooks.org" in ok["domains"]
    assert json.dumps(ok)  # ответ сериализуется
