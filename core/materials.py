"""Импорт материала пользователя: PDF/Markdown → текст → фрагменты (T-0014, R-0011).

Фрагмент — единица, на которую ссылаются узлы и вопросы, построенные из материала
(T-0015, T-0016): теория узла указывает `fragmentId`, и человек видит, откуда взято.
Поэтому идентификатор фрагмента стабилен внутри материала и не меняется при чтении.
Модуль не знает ни предметов, ни модулей: разбор — чистая функция над байтами.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import PurePath

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Целевой размер фрагмента: достаточно для узла, но не страница целиком.
CHUNK_CHARS = 1200

MARKDOWN_EXT = {".md", ".markdown", ".txt"}
PDF_EXT = {".pdf"}


class UnsupportedFile(Exception):
    """Тип файла не поддерживается."""


class NothingToExtract(Exception):
    """Файл разобран, но текста в нём нет (скан без текстового слоя, пустой файл)."""


@dataclass
class Fragment:
    id: str
    text: str
    heading: str = ""
    page: int | None = None

    def dump(self) -> dict:
        out: dict = {"id": self.id, "text": self.text}
        if self.heading:
            out["heading"] = self.heading
        if self.page is not None:
            out["page"] = self.page
        return out


@dataclass
class Extracted:
    source: str  # pdf | markdown
    title: str
    fragments: list[Fragment] = field(default_factory=list)
    pages: int | None = None


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _chunk(paragraphs: list[str]) -> list[str]:
    """Склеить короткие абзацы, разрезать длинные: фрагмент ≈ CHUNK_CHARS."""
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # Длинный абзац режем по предложениям, а не посреди слова.
        pieces = [p]
        if len(p) > CHUNK_CHARS:
            pieces = []
            cur = ""
            for sentence in re.split(r"(?<=[.!?…])\s+", p):
                if cur and len(cur) + len(sentence) + 1 > CHUNK_CHARS:
                    pieces.append(cur)
                    cur = sentence
                else:
                    cur = f"{cur} {sentence}".strip()
            if cur:
                pieces.append(cur)
        for piece in pieces:
            if buf and len(buf) + len(piece) + 2 > CHUNK_CHARS:
                chunks.append(buf)
                buf = piece
            else:
                buf = f"{buf}\n\n{piece}".strip()
    if buf:
        chunks.append(buf)
    return chunks


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _from_markdown(text: str, filename: str) -> Extracted:
    title = ""
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            heading = m.group(2).strip()
            if not title and len(m.group(1)) == 1:
                title = heading
            sections.append((heading, []))
        else:
            sections[-1][1].append(line)

    fragments: list[Fragment] = []
    for heading, lines in sections:
        # Абзацы — через пустую строку; код внутри блока остаётся целым.
        body = "\n".join(lines)
        paragraphs = re.split(r"\n\s*\n", body)
        for chunk in _chunk(paragraphs):
            fragments.append(Fragment(id=f"f{len(fragments) + 1}", text=chunk, heading=heading))
    return Extracted(
        source="markdown",
        title=title or PurePath(filename).stem,
        fragments=fragments,
    )


def _from_pdf(data: bytes, filename: str) -> Extracted:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise NothingToExtract("PDF защищён паролем: прочитать текст нельзя")
        pages = list(reader.pages)
        meta_title = (reader.metadata.title or "").strip() if reader.metadata else ""
    except PdfReadError as e:
        raise UnsupportedFile(f"Не удалось открыть PDF: {e}") from e

    fragments: list[Fragment] = []
    for number, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — одна битая страница не должна ронять весь файл
            continue
        # PDF переносит строки по ширине страницы: абзац — это пустая строка.
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        for chunk in _chunk(re.split(r"\n\s*\n", text)):
            fragments.append(Fragment(id=f"f{len(fragments) + 1}", text=chunk, page=number))
    return Extracted(
        source="pdf",
        title=meta_title or PurePath(filename).stem,
        fragments=fragments,
        pages=len(pages),
    )


def extract(filename: str, data: bytes) -> Extracted:
    ext = PurePath(filename or "").suffix.lower()
    if ext in PDF_EXT:
        result = _from_pdf(data, filename)
    elif ext in MARKDOWN_EXT:
        result = _from_markdown(_decode(data), filename)
    else:
        raise UnsupportedFile("Поддерживаются PDF, Markdown и текстовые файлы (.md, .txt)")
    if not result.fragments:
        raise NothingToExtract(
            "В файле нет текста, который можно прочитать (скан без текстового слоя или пустой файл)"
        )
    return result
