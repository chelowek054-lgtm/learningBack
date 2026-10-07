"""Сопоставление профиля с графом по близости векторов (T-0096, A-0031)."""

from __future__ import annotations

import math

import pytest

from core.config import settings
from modules.knowledge import domains, profile_match, profile_store
from modules.knowledge.models import Concept, SkillProfile
from tests.conftest import make_user

DIM = 1024


class AngleEmbedder:
    """Вектор лежит на единичной окружности: угол задаёт таблица по началу текста. Близость = cos(Δугла)."""

    model = "angle"
    dim = DIM

    def __init__(self, angles: dict[str, float]):
        self.angles = angles

    def embed(self, texts):
        out = []
        for text in texts:
            angle = next((a for prefix, a in self.angles.items() if text.startswith(prefix)), 90.0)
            vec = [0.0] * DIM
            vec[0], vec[1] = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            out.append(vec)
        return out


class Judge:
    def __init__(self, verdict):
        self.verdict, self.calls = verdict, 0

    def structured(self, tool, desc, schema, prompt):
        self.calls += 1
        return {"verdict": self.verdict, "reason": "тест"}


def area(title, summary="описание", key=None, concepts=()):
    return {
        "key": key or title.lower(),
        "title": title,
        "summary": summary,
        "concepts": list(concepts),
    }


@pytest.fixture
def registry(session):
    domains.register(session, "Линейная алгебра", aliases=["линал"], foundation=True)
    return session


def cos_for(angle):  # близость при заданном угле между векторами
    return math.cos(math.radians(angle))


def test_thresholds_make_sense():
    assert settings.area_maybe_similarity < settings.area_same_similarity <= 1.0


def test_exact_name_or_alias_needs_no_vectors(registry):
    boom = AngleEmbedder({})
    boom.embed = lambda texts: (_ for _ in ()).throw(AssertionError("вектор не нужен"))
    for title in ("линейная алгебра", "Линал"):
        d = profile_match.match_area(registry, area(title), None, boom)
        assert d == {
            "decision": "existing",
            "domain": domains.normalize("Линейная алгебра"),
            "similarity": 1.0,
            "via": "name",
        }


def test_close_vector_means_the_area_already_exists(registry):
    # угол 20° → близость ≈ 0.94 ≥ 0.82
    emb = AngleEmbedder({"Линейная алгебра": 0.0, "Матрицы": 20.0})
    d = profile_match.match_area(registry, area("Матрицы"), None, emb)
    assert (
        d["decision"] == "existing"
        and d["via"] == "vector"
        and d["domain"] == domains.normalize("Линейная алгебра")
    )
    assert d["similarity"] == pytest.approx(cos_for(20), abs=1e-3)


def test_far_vector_means_a_new_area(registry):
    emb = AngleEmbedder({"Линейная алгебра": 0.0, "Кулинария": 80.0})
    d = profile_match.match_area(registry, area("Кулинария"), None, emb)
    assert d["decision"] == "new" and d["via"] == "none" and "disputed" not in d


def test_disputed_band_is_decided_by_the_model(registry):
    emb = AngleEmbedder(
        {"Линейная алгебра": 0.0, "Векторы": 34.0}
    )  # cos ≈ 0.83? нет: 0.829 → выше same
    emb.angles["Векторы"] = 40.0  # cos ≈ 0.766: между 0.70 и 0.82
    same, different = Judge("same"), Judge("different")

    yes = profile_match.match_area(registry, area("Векторы"), same, emb)
    no = profile_match.match_area(registry, area("Векторы"), different, emb)

    assert (yes["decision"], yes["via"], yes["disputed"]) == ("existing", "model", True)
    assert (no["decision"], no["via"], no["disputed"]) == ("new", "model", True)
    assert same.calls == 1 and different.calls == 1


def test_disputed_without_a_model_stays_new_but_is_marked(registry):
    emb = AngleEmbedder({"Линейная алгебра": 0.0, "Векторы": 40.0})
    d = profile_match.match_area(registry, area("Векторы"), None, emb)
    assert d["decision"] == "new" and d["disputed"] is True and d["via"] == "none"


def test_empty_registry_means_new(session):
    d = profile_match.match_area(session, area("Что угодно"), None, AngleEmbedder({}))
    assert d["decision"] == "new" and d["similarity"] == 0.0


def test_domain_vectors_are_cached_and_refreshed_when_text_changes(registry):
    emb = AngleEmbedder({})
    assert profile_match.embed_domains(registry, emb) == 1
    assert profile_match.embed_domains(registry, emb) == 0  # текст не менялся
    registry.add(
        Concept(
            domain="Линейная алгебра",
            title="Новое понятие",
            tier="core",
            content={},
            bloom_levels=[],
        )
    )
    registry.flush()
    assert (
        profile_match.embed_domains(registry, emb) == 1
    )  # у области появились понятия — текст другой


def test_concepts_of_an_existing_area_are_matched_one_by_one(registry):
    registry.add_all(
        [
            Concept(
                domain="Линейная алгебра",
                title="Определитель",
                tier="core",
                content={"summary": "Число, характеризующее матрицу."},
                bloom_levels=[],
            ),
            Concept(
                domain="Линейная алгебра",
                title="След",
                tier="core",
                content={"summary": "Сумма диагонали."},
                bloom_levels=[],
            ),
        ]
    )
    registry.flush()
    emb = AngleEmbedder({"Определитель": 0.0, "След": 90.0, "Det": 5.0, "Ранг": 45.0})
    domain = domains.resolve(registry, "Линейная алгебра")
    found = profile_match.match_concepts(
        registry,
        domain,
        [
            {"key": "det", "title": "Det", "summary": "то же"},
            {"key": "rank", "title": "Ранг", "summary": "новое"},
        ],
        emb,
    )
    assert [(c["key"], c["decision"]) for c in found] == [("det", "existing"), ("rank", "new")]
    assert found[0]["conceptId"] and found[1]["conceptId"] is None


def test_match_profile_collects_decisions_and_summary(registry):
    emb = AngleEmbedder({"Линейная алгебра": 0.0, "Кулинария": 80.0})
    profile = {"areas": [area("Линейная алгебра"), area("Кулинария")]}
    out = profile_match.match_profile(registry, profile, None, emb)
    assert [a["decision"] for a in out["areas"]] == ["existing", "new"]
    assert out["summary"] == {"existing": 1, "new": 1, "disputed": 0, "unavailable": 0}


def test_stored_profile_keeps_the_decisions_until_it_is_edited(session, client, monkeypatch):
    from modules.knowledge import skill_profile

    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    monkeypatch.setattr(profile_match, "get_embedder", lambda: AngleEmbedder({}))
    user = make_user(session)
    row = SkillProfile(user_id=user.id, domain="ml", status="draft")
    row.profile = skill_profile.build_profile("Навык", "ctx", "apply")
    session.add(row)
    session.flush()
    assert row.profile.get("match") is None

    decisions = profile_store.match(session, row)
    assert row.profile["match"] == decisions and decisions["summary"]["new"] == 2

    profile_store.save_edit(session, row, row.profile)
    assert "match" not in row.profile  # после правки решения устарели


def test_api_match_requires_a_profile(session, client):
    api = client(make_user(session))
    assert api.post("/graph/profile/ml/match").status_code == 404


def test_unavailable_vectors_do_not_break_matching(registry):
    from core.ai_base import ProviderError

    class Down:
        model, dim = "down", DIM

        def embed(self, texts):
            raise ProviderError("Эмбеддинги недоступны")

    profile = {"areas": [area("Кулинария")]}
    out = profile_match.match_profile(registry, profile, None, Down())

    assert out["areas"][0]["decision"] == "new" and out["areas"][0]["unavailable"] is True
    assert out["summary"]["unavailable"] == 1


def test_unavailable_vectors_make_concepts_new_too(registry):
    from core.ai_base import ProviderError

    registry.add(
        Concept(
            domain="Линейная алгебра",
            title="Определитель",
            tier="core",
            content={},
            bloom_levels=[],
        )
    )
    registry.flush()

    class Down:
        model, dim = "down", DIM

        def embed(self, texts):
            raise ProviderError("нет сети")

    domain = domains.resolve(registry, "Линейная алгебра")
    found = profile_match.match_concepts(registry, domain, [{"key": "k", "title": "Det"}], Down())
    assert [(c["key"], c["decision"]) for c in found] == [("k", "new")]
