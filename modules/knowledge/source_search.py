"""Поиск источников для графа: белый список каталогов и веб-поиск (T-0082, A-0027, A-0028, R-0043).

Что не хватает в графе, ищется сначала в каталогах открытых источников (Wikibooks, arXiv), потом
через API веб-поиска — и только среди разрешённых доменов. Решение владельца: берём, докуда
дотянемся, но риск управляем: скачивать можно только с белого списка доменов (редирект на чужой
домен — отказ), с лимитом размера, числа документов на запрос и временем ожидания; у каждого
найденного документа сохраняется происхождение (адрес, лицензия, поставщик), чтобы его можно было
отозвать вместе с выведенным из него (provenance.remove_document).

Найденное попадает туда же, куда ручная загрузка: документ → фрагменты → задача разбора.
"""

from __future__ import annotations

import logging
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from core.config import settings
from modules.knowledge import ingest, provenance
from modules.knowledge.models import SourceDocument

log = logging.getLogger("praxis.source_search")

USER_AGENT = "PraxisSourceBot/1.0 (+education; contact: admin)"
ATOM = {"a": "http://www.w3.org/2005/Atom"}


class SearchError(ValueError):
    """Нельзя найти или скачать; `code` — для тестов и ответа API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Candidate:
    """Найденный документ: пока только описание, ничего не скачано."""

    title: str
    url: str
    provider: str
    license: str
    kind: str  # wikitext | pdf | html
    summary: str = ""

    def dump(self) -> dict[str, Any]:
        return asdict(self)


# ---- белый список ----


def allowed_domains() -> set[str]:
    return {d.strip().lower() for d in settings.source_domains.split(",") if d.strip()}


def host_allowed(url: str, domains: set[str] | None = None) -> bool:
    """Домен из белого списка или его поддомен; схема только http(s)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == d or host.endswith("." + d) for d in (domains or allowed_domains()))


def _client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(settings.source_fetch_timeout),
        transport=transport,
        follow_redirects=False,  # редиректы идём сами: каждый шаг проверяется по белому списку
    )


# ---- поставщики ----


class Finder(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[Candidate]: ...


class WikibooksFinder:
    """Wikibooks (CC BY-SA): поиск по MediaWiki API, текст — сырой викитекст страницы."""

    name = "wikibooks"
    HOSTS = ("en.wikibooks.org", "ru.wikibooks.org")

    def __init__(
        self, transport: httpx.BaseTransport | None = None, hosts: tuple[str, ...] | None = None
    ):
        self._transport = transport
        self._hosts = hosts or self.HOSTS

    def search(self, query: str, limit: int) -> list[Candidate]:
        out: list[Candidate] = []
        with _client(self._transport) as c:
            for host in self._hosts:
                if not host_allowed(f"https://{host}/"):
                    continue
                r = c.get(
                    f"https://{host}/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": query,
                        "srlimit": limit,
                        "format": "json",
                    },
                )
                r.raise_for_status()
                for hit in r.json().get("query", {}).get("search", []):
                    title = hit.get("title", "")
                    if not title or hit.get("size", 0) < 1500:  # заготовки без содержания не нужны
                        continue
                    snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
                    out.append(
                        Candidate(
                            title=f"{title} (Wikibooks)",
                            url=f"https://{host}/w/index.php?title={title.replace(' ', '_')}&action=raw",
                            provider=self.name,
                            license="CC BY-SA",
                            kind="wikitext",
                            summary=snippet[:200],
                        )
                    )
        return out[:limit]


class ArxivFinder:
    """arXiv: поиск по Atom API, документ — PDF статьи. Лицензия зависит от статьи — помечаем «arXiv»."""

    name = "arxiv"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._transport = transport

    def search(self, query: str, limit: int) -> list[Candidate]:
        with _client(self._transport) as c:
            r = c.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": f'all:"{query}"', "max_results": limit},
                follow_redirects=True,
            )
            r.raise_for_status()
        out: list[Candidate] = []
        for entry in ET.fromstring(r.text).findall("a:entry", ATOM):
            title = re.sub(r"\s+", " ", entry.findtext("a:title", "", ATOM)).strip()
            pdf = next(
                (
                    link.get("href", "")
                    for link in entry.findall("a:link", ATOM)
                    if link.get("type") == "application/pdf"
                ),
                "",
            )
            if title and pdf:
                out.append(
                    Candidate(
                        title=f"{title} (arXiv)",
                        url=pdf if pdf.endswith(".pdf") else pdf + ".pdf",
                        provider=self.name,
                        license="arXiv (лицензия статьи — на странице работы)",
                        kind="pdf",
                        summary=re.sub(r"\s+", " ", entry.findtext("a:summary", "", ATOM)).strip()[
                            :200
                        ],
                    )
                )
        return out[:limit]


class BraveFinder:
    """API веб-поиска Brave (нужен ключ): выдача фильтруется белым списком доменов."""

    name = "brave"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._transport = transport

    def search(self, query: str, limit: int) -> list[Candidate]:
        domains = allowed_domains()
        # Просим у поиска только разрешённые сайты: иначе выдача уйдёт в чужое и отсеется целиком.
        scoped = f"{query} " + " OR ".join(f"site:{d}" for d in sorted(domains))
        with _client(self._transport) as c:
            r = c.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": scoped, "count": min(20, limit * 3)},
                headers={
                    "X-Subscription-Token": settings.brave_api_key,
                    "Accept": "application/json",
                },
            )
            r.raise_for_status()
        out: list[Candidate] = []
        for item in r.json().get("web", {}).get("results", []):
            url = item.get("url", "")
            if host_allowed(url, domains):
                out.append(
                    Candidate(
                        title=str(item.get("title", ""))[:200] or url,
                        url=url,
                        provider=self.name,
                        license="не определена",
                        kind="pdf" if url.lower().endswith(".pdf") else "html",
                        summary=str(item.get("description", ""))[:200],
                    )
                )
        return out[:limit]


def default_finders() -> list[Finder]:
    """Каталоги первыми (бесплатно и надёжно по лицензиям), веб-поиск — дополнением, если есть ключ."""
    finders: list[Finder] = [WikibooksFinder(), ArxivFinder()]
    if settings.brave_api_key:
        finders.append(BraveFinder())
    return finders


def search(
    query: str, limit: int = 5, finders: list[Finder] | None = None
) -> tuple[list[Candidate], list[str]]:
    """Кандидаты по всем поставщикам без повторов по адресу и описания сбоев поставщиков."""
    query = query.strip()
    if len(query) < 3:
        raise SearchError("short_query", "Запрос слишком короткий")
    found: list[Candidate] = []
    problems: list[str] = []
    seen: set[str] = set()
    for finder in finders if finders is not None else default_finders():
        try:
            for cand in finder.search(query, limit):
                if cand.url not in seen and host_allowed(cand.url):
                    seen.add(cand.url)
                    found.append(cand)
        except Exception as exc:  # noqa: BLE001 — один недоступный каталог не должен ронять поиск
            problems.append(f"{finder.name}: {exc}")
            log.warning("поставщик %s недоступен: %s", finder.name, exc)
    return found, problems


# ---- скачивание ----


def wikitext_to_markdown(text: str) -> str:
    """Викитекст → Markdown ровно настолько, чтобы разбор увидел главы и абзацы."""
    out = re.sub(r"\{\{[^{}]*\}\}", "", text)
    out = re.sub(r"(?m)^=+\s*([^=]+?)\s*=+\s*$", lambda m: "# " + m.group(1), out)
    out = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", out)
    out = out.replace("'''", "").replace("''", "")
    out = re.sub(
        r"<pre>(.*?)</pre>", lambda m: "```\n" + m.group(1).strip("\n") + "\n```", out, flags=re.S
    )
    out = re.sub(r"<(?!/?(?:code)\b)[^>]+>", "", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"


def fetch(candidate: Candidate, transport: httpx.BaseTransport | None = None) -> tuple[str, bytes]:
    """Скачать документ: (имя файла, байты). Только белый список, с проверкой каждого редиректа."""
    url = candidate.url
    if not host_allowed(url):
        raise SearchError(
            "domain_not_allowed", f"Домен не из белого списка: {urlparse(url).hostname}"
        )
    limit = settings.source_fetch_max_bytes
    with _client(transport) as c:
        for _ in range(4):  # не больше трёх редиректов
            with c.stream("GET", url) as r:
                if r.is_redirect:
                    url = str(httpx.URL(url).join(r.headers.get("location", "")))
                    if not host_allowed(url):
                        raise SearchError(
                            "domain_not_allowed", "Редирект ведёт на домен вне белого списка"
                        )
                    continue
                r.raise_for_status()
                declared = int(r.headers.get("content-length") or 0)
                if declared > limit:
                    raise SearchError("too_large", f"Файл больше {limit // (1024 * 1024)} МБ")
                data = bytearray()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > limit:
                        raise SearchError("too_large", f"Файл больше {limit // (1024 * 1024)} МБ")
                break
        else:
            raise SearchError("too_many_redirects", "Слишком много редиректов")
    if not data:
        raise SearchError("empty", "Документ пустой")
    body = bytes(data)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", candidate.title).strip("_")[:60] or "source"
    if candidate.kind == "wikitext":
        return f"{slug}.md", wikitext_to_markdown(body.decode("utf-8", errors="replace")).encode(
            "utf-8"
        )
    if candidate.kind == "pdf":
        return f"{slug}.pdf", body
    raise SearchError(
        "unsupported_kind", "HTML-страницы пока не разбираются: нужен PDF, Markdown или текст"
    )


# ---- в граф ----


def known_urls(session: Session) -> set[str]:
    return {
        u
        for (u,) in session.query(SourceDocument.origin_url).filter(
            SourceDocument.origin_url.isnot(None)
        )
    }


def fetch_and_queue(
    session: Session,
    user_id: uuid.UUID,
    domain: str,
    candidates: list[Candidate],
    *,
    transport: httpx.BaseTransport | None = None,
    max_docs: int | None = None,
) -> dict[str, Any]:
    """Скачать кандидатов и поставить разбор. Лимит документов на запрос; уже известные пропускаются."""
    budget = max_docs if max_docs is not None else settings.source_max_docs_per_request
    queued: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    known = known_urls(session)
    for cand in candidates:
        if len(queued) >= budget:
            skipped.append({"url": cand.url, "reason": "превышен лимит документов на запрос"})
            continue
        if cand.url in known:
            skipped.append({"url": cand.url, "reason": "уже загружен"})
            continue
        try:
            filename, data = fetch(cand, transport)
            doc, created = provenance.add_document(
                session,
                title=cand.title,
                data=data,
                added_by=user_id,
                domain=domain,
                license=cand.license,
                origin_url=cand.url,
                meta={"filename": filename, "provider": cand.provider},
            )
            if created:
                ingest.parse_document(session, doc, filename, data)
                ingest.enqueue_ingest(session, doc, user_id)
        except (SearchError, provenance.ProvenanceError, httpx.HTTPError) as exc:
            skipped.append({"url": cand.url, "reason": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 — нечитаемый файл (скан, битый PDF) не роняет остальные
            skipped.append({"url": cand.url, "reason": f"не удалось разобрать: {exc}"})
            continue
        queued.append({"documentId": str(doc.id), "title": doc.title, "created": created})
        known.add(cand.url)
    return {"queued": queued, "skipped": skipped}
