"""Раздел админки «Граф знаний»: схема канона вместо трёх таблиц (T-0107…T-0110, R-0058).

Страница и JSON-эндпоинты живут под тем же входом администратора, что и вся админка. Логика чтения и правок —
в `graph_admin_data`; здесь только HTTP и перевод ошибок правки в понятные ответы.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, FileSystemLoader
from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.db import SessionLocal
from modules.knowledge import graph_admin_data as data

TEMPLATES_DIR = str(Path(__file__).parent / "templates")


def _reviewer(request: Request) -> uuid.UUID | None:
    raw = request.session.get("admin_user_id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _run(work: Callable[[Any], Any], status: int = 200) -> Response:
    """Одна транзакция на запрос; отказ правки — 422 с кодом и текстом для интерфейса."""
    with SessionLocal() as session:
        try:
            result = work(session)
            session.commit()
        except data.GraphEditError as e:
            session.rollback()
            return JSONResponse({"error": e.code, "message": str(e)}, status_code=422)
    return JSONResponse(result, status_code=status)


class GraphAdmin(BaseView):
    name = "Граф знаний"
    icon = "fa-solid fa-circle-nodes"
    category = "Модель знаний"

    @expose("/graph", methods=["GET"])
    async def page(self, request: Request):
        # Шаблон лежит в модуле, а не в ядре: добавляем его каталог к загрузчику админки один раз.
        env = self.templates.env
        if not getattr(env, "_graph_templates", False):
            env.loader = ChoiceLoader([env.loader, FileSystemLoader(TEMPLATES_DIR)])
            env._graph_templates = True  # type: ignore[attr-defined]
        return await self.templates.TemplateResponse(request, "graph.html")

    @expose("/graph/api/overview", methods=["GET"])
    async def overview(self, request: Request):
        return _run(data.overview)

    @expose("/graph/api/area", methods=["GET"])
    async def area(self, request: Request):
        domain = request.query_params.get("domain", "")
        return _run(lambda s: data.area_graph(s, domain))

    @expose("/graph/api/areas", methods=["GET"])
    async def areas(self, request: Request):
        return _run(data.domains_graph)

    @expose("/graph/api/bridges", methods=["GET"])
    async def bridges(self, request: Request):
        domain = request.query_params.get("domain", "")
        return _run(lambda s: data.bridges_graph(s, domain))

    @expose("/graph/api/node/{node_id}", methods=["GET"])
    async def node_get(self, request: Request):
        node_id = request.path_params["node_id"]
        return _run(lambda s: data.node_card(s, node_id))

    @expose("/graph/api/node/{node_id}", methods=["POST"])
    async def node_set(self, request: Request):
        node_id, fields, who = (
            request.path_params["node_id"],
            await _body(request),
            _reviewer(request),
        )
        return _run(lambda s: data.update_node(s, node_id, fields, who))

    @expose("/graph/api/edge", methods=["POST"])
    async def edge_add(self, request: Request):
        b = await _body(request)
        return _run(
            lambda s: data.add_edge(
                s, str(b.get("from", "")), str(b.get("to", "")), str(b.get("type", ""))
            ),
            status=201,
        )

    @expose("/graph/api/edge/{edge_id}", methods=["POST"])
    async def edge_set(self, request: Request):
        edge_id, fields, who = (
            request.path_params["edge_id"],
            await _body(request),
            _reviewer(request),
        )
        return _run(lambda s: data.change_edge(s, edge_id, fields, who))

    @expose("/graph/api/edge/{edge_id}/delete", methods=["POST"])
    async def edge_delete(self, request: Request):
        edge_id = request.path_params["edge_id"]
        return _run(lambda s: data.delete_edge(s, edge_id) or {"ok": True})
