"""Лицензии и видимость источников учащемуся (V-0131, R-0072, T-0152, T-0153).

Ручная часть (ссылка в приложении открывает оригинал на нужной странице) остаётся за человеком.
"""

from __future__ import annotations

import pytest

from core.config import settings
from modules.knowledge import funnel
from tests.conftest import make_user
from tests.kg_helpers import Gateway, claim, concept, document

TEXT = (
    "The theory of groups studies sets with an operation and it is the basis of the algebra. " * 4
)


@pytest.fixture
def funnel_on(monkeypatch):
    monkeypatch.setattr(settings, "funnel_enabled", True)


def test_license_outside_the_list_is_refused_with_reason():
    ok, why = funnel.license_ok("all-rights-reserved")
    assert not ok and "не входит" in why


def test_missing_license_is_refused():
    ok, why = funnel.license_ok(None)
    assert not ok and "не указана" in why


def test_allowed_license_passes_in_any_case():
    assert funnel.license_ok("CC-BY")[0]
    assert funnel.license_ok("Public-Domain")[0]


def test_screen_rejects_by_license_without_asking_the_model(session, funnel_on):
    doc, frags = document(session, license="proprietary", texts=[TEXT] * 5)
    gw = Gateway()  # любой вызов — ошибка
    result = funnel.screen(session, doc, frags, gw)
    assert not result.ok and result.step == "license"
    assert gw.calls == 0
    assert doc.quarantined and "не входит" in " ".join(doc.trust_reasons)


def test_learner_gets_link_license_and_attribution(session, client):
    doc, frags = document(
        session, title="Algebra Book", license="cc-by", origin_url="https://x.org/a.pdf"
    )
    c = concept(session, "Group")
    cl = claim(session, c, "A group has an identity.", frags[0], quote="the quote of the book")
    user = make_user(session)
    r = client(user).get(f"/graph/nodes/{c.id}/claims", params={"lang": "en"})
    assert r.status_code == 200
    item = r.json()[0]
    assert item["id"] == str(cl.id)
    src = item["sources"][0]
    assert src["title"] == "Algebra Book" and src["license"] == "cc-by"
    assert src["url"] == "https://x.org/a.pdf#page=1"
    assert src["quote"] == "the quote of the book"


def test_pdf_link_points_to_the_page(session, client):
    doc, frags = document(
        session, title="B", origin_url="https://x.org/a.pdf", texts=["p one", "p two"]
    )
    c = concept(session, "Group")
    claim(session, c, "Second page fact.", frags[1])
    src = client(make_user(session)).get(f"/graph/nodes/{c.id}/claims").json()[0]["sources"][0]
    assert src["url"] == "https://x.org/a.pdf#page=2"


def test_quote_is_hidden_when_the_license_does_not_allow_reproduction(session, client):
    doc, frags = document(session, license="proprietary", origin_url="https://x.org/b")
    c = concept(session, "Group")
    claim(session, c, "Fact.", frags[0], quote="do not reproduce this quote")
    src = client(make_user(session)).get(f"/graph/nodes/{c.id}/claims").json()[0]["sources"][0]
    assert src["quote"] is None and src["title"] == doc.title


def test_quarantined_source_is_not_shown_to_learner(session, client):
    doc, frags = document(session)
    doc.quarantined = True
    c = concept(session, "Group")
    claim(session, c, "Fact.", frags[0])
    item = client(make_user(session)).get(f"/graph/nodes/{c.id}/claims").json()[0]
    assert item["sources"] == []
