"""Импорт материала: PDF/Markdown → фрагменты (T-0014, R-0011)."""

from __future__ import annotations

import io

import pytest

from core.materials import (
    CHUNK_CHARS,
    MAX_UPLOAD_BYTES,
    NothingToExtract,
    UnsupportedFile,
    extract,
)
from core.models import Material
from tests.conftest import make_user


def make_pdf(pages: list[str]) -> bytes:
    """Минимальный валидный PDF с текстом (латиница): по странице на строку списка."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    kids_placeholder = len(objs) + 1  # id узла Pages будет известен после страниц
    pages_id = kids_placeholder + 2 * len(pages)
    for text in pages:
        stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        page_ids.append(page)
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    assert add(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()) == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


MD = """# Градиентный спуск

Метод оптимизации: идём против градиента функции потерь.

## Шаг обучения

Слишком большой шаг расходится, слишком малый — сходится медленно.

```python
# заголовок внутри кода — не заголовок
# w -= lr * grad
```

## Итоги

Подбирайте шаг по валидации.
"""


# ---- разбор текста ----


def test_markdown_splits_by_headings_and_titles_the_document():
    r = extract("gd.md", MD.encode())

    assert r.source == "markdown" and r.title == "Градиентный спуск"
    assert [f.heading for f in r.fragments] == [
        "Градиентный спуск",
        "Шаг обучения",
        "Итоги",
    ]
    assert [f.id for f in r.fragments] == ["f1", "f2", "f3"]


def test_heading_inside_code_block_is_not_a_heading():
    r = extract("gd.md", MD.encode())

    step = next(f for f in r.fragments if f.heading == "Шаг обучения")
    assert "# заголовок внутри кода" in step.text
    assert all(f.heading != "заголовок внутри кода — не заголовок" for f in r.fragments)


def test_title_falls_back_to_filename_without_h1():
    assert extract("notes.txt", b"just a note").title == "notes"


def test_long_paragraph_is_split_on_sentence_boundaries():
    text = " ".join(f"Предложение номер {i} про граф знаний." for i in range(120))

    r = extract("long.md", text.encode())

    assert len(r.fragments) > 1
    assert all(len(f.text) <= CHUNK_CHARS for f in r.fragments)
    assert all(f.text.rstrip().endswith(".") for f in r.fragments)


def test_short_paragraphs_are_merged_into_one_fragment():
    r = extract("a.md", b"one\n\ntwo\n\nthree")

    assert len(r.fragments) == 1 and r.fragments[0].text == "one\n\ntwo\n\nthree"


def test_cp1251_text_is_decoded():
    r = extract("old.txt", "Привет, граф".encode("cp1251"))

    assert r.fragments[0].text == "Привет, граф"


def test_pdf_text_is_extracted_with_page_numbers():
    r = extract("book.pdf", make_pdf(["Hello from page one", "Second page text"]))

    assert r.source == "pdf" and r.pages == 2 and r.title == "book"
    assert [(f.page, f.text) for f in r.fragments] == [
        (1, "Hello from page one"),
        (2, "Second page text"),
    ]


def test_pdf_without_text_layer_is_reported():
    with pytest.raises(NothingToExtract):
        extract("scan.pdf", make_pdf([""]))


def test_garbage_pdf_and_unknown_extension_are_unsupported():
    with pytest.raises(UnsupportedFile):
        extract("x.pdf", b"not a pdf")
    with pytest.raises(UnsupportedFile):
        extract("x.docx", b"whatever")


def test_empty_text_file_has_nothing_to_extract():
    with pytest.raises(NothingToExtract):
        extract("empty.md", b"   \n\n  ")


# ---- API ----


def _upload(api, name, data, **form):
    form.setdefault("module", "knowledge")
    return api.post("/content/materials", files={"file": (name, data)}, data=form)


def test_module_is_required(session, client):
    r = client(make_user(session)).post("/content/materials", files={"file": ("a.md", b"x")})

    assert r.status_code == 422


def test_upload_markdown_creates_personal_material(session, client):
    user = make_user(session)

    r = _upload(client(user), "gd.md", MD.encode())

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source"] == "markdown" and body["title"] == "Градиентный спуск"
    assert body["fragmentCount"] == 3 and body["mine"] is True
    stored = session.get(Material, body["id"])
    assert stored.user_id == user.id
    assert stored.content["fragments"][0]["id"] == "f1"


def test_upload_pdf_and_custom_title(session, client):
    r = _upload(client(make_user(session)), "b.pdf", make_pdf(["Alpha beta"]), title="Моя книга")

    assert r.status_code == 201
    assert r.json()["title"] == "Моя книга" and r.json()["pages"] == 1


def test_oversized_file_is_rejected(session, client, monkeypatch):
    monkeypatch.setattr("core.routers.content.MAX_UPLOAD_BYTES", 100)

    r = _upload(client(make_user(session)), "big.md", b"x" * 101)

    assert r.status_code == 413


def test_file_at_the_limit_is_accepted(session, client, monkeypatch):
    monkeypatch.setattr("core.routers.content.MAX_UPLOAD_BYTES", 100)

    assert _upload(client(make_user(session)), "ok.md", b"x" * 100).status_code == 201


def test_upload_limit_is_twenty_megabytes():
    assert MAX_UPLOAD_BYTES == 20 * 1024 * 1024


@pytest.mark.parametrize(
    "name,data,code",
    [("x.docx", b"data", 415), ("empty.md", b"", 422), ("blank.md", b"  \n ", 422)],
)
def test_bad_uploads_get_clear_errors(session, client, name, data, code):
    assert _upload(client(make_user(session)), name, data).status_code == code


def test_list_shows_summary_for_imported_without_fragments(session, client):
    api = client(make_user(session))
    _upload(api, "gd.md", MD.encode())

    [row] = api.get("/content/materials").json()

    assert row["fragmentCount"] == 3 and "content" not in row


def test_detail_returns_fragments_and_hides_foreign_material(session, client):
    owner, stranger = make_user(session), make_user(session)
    mid = _upload(client(owner), "gd.md", MD.encode()).json()["id"]

    mine = client(owner).get(f"/content/materials/{mid}").json()
    assert [f["id"] for f in mine["content"]["fragments"]] == ["f1", "f2", "f3"]
    assert client(stranger).get(f"/content/materials/{mid}").status_code == 404
    assert client(stranger).get("/content/materials").json() == []


def test_delete_own_material_only(session, client):
    owner, stranger = make_user(session), make_user(session)
    mid = _upload(client(owner), "gd.md", MD.encode()).json()["id"]

    client(stranger).delete(f"/content/materials/{mid}")
    assert session.get(Material, mid) is not None
    assert client(owner).delete(f"/content/materials/{mid}").status_code == 204
    session.expire_all()
    assert session.get(Material, mid) is None


def test_shared_material_is_visible_but_not_deletable(session, client):
    session.add(
        Material(
            user_id=None, module="knowledge", source="seed", title="Общий", content={"text": "t"}
        )
    )
    session.flush()
    api = client(make_user(session))

    [row] = api.get("/content/materials").json()
    assert row["mine"] is False and row["content"] == {"text": "t"}
    assert api.delete(f"/content/materials/{row['id']}").status_code == 204
    assert len(api.get("/content/materials").json()) == 1


def test_upload_requires_login(session):
    from fastapi.testclient import TestClient

    from core.app import app

    r = TestClient(app).post("/content/materials", files={"file": ("a.md", b"x")})
    assert r.status_code in (401, 403)
