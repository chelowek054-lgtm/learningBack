"""Выдача и импорт контента (материалы, рубрики). WS4/WS2 — защищено токеном."""

import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from core.deps import CurrentUser, SessionDep
from core.materials import (
    MAX_UPLOAD_BYTES,
    NothingToExtract,
    UnsupportedFile,
    extract,
)
from core.models import Material

router = APIRouter(prefix="/content", tags=["content"])

# Материалы, импортированные пользователем: в списке отдаём только сводку,
# фрагменты — отдельным запросом (документ на сотни страниц в списке не нужен).
IMPORTED = ("pdf", "markdown")


def _summary(m: Material) -> dict:
    fragments = m.content.get("fragments") if isinstance(m.content, dict) else None
    return {
        "id": str(m.id),
        "module": m.module,
        "source": m.source,
        "title": m.title,
        "fragmentCount": len(fragments) if isinstance(fragments, list) else None,
        "pages": m.content.get("pages") if isinstance(m.content, dict) else None,
        "mine": m.user_id is not None,
    }


@router.get("/materials")
def list_materials(user: CurrentUser, session: SessionDep) -> list[dict]:
    rows = (
        session.query(Material)
        .filter((Material.user_id == user.id) | (Material.user_id.is_(None)))
        .all()
    )
    return [
        {**_summary(m), "content": m.content} if m.source not in IMPORTED else _summary(m)
        for m in rows
    ]


@router.get("/materials/{material_id}")
def get_material(material_id: str, user: CurrentUser, session: SessionDep) -> dict:
    try:
        mid = uuid.UUID(material_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "материал не найден") from None
    m = session.get(Material, mid)
    # Чужой личный материал неотличим от несуществующего.
    if m is None or (m.user_id is not None and m.user_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "материал не найден")
    return {**_summary(m), "content": m.content}


@router.post("/materials", status_code=status.HTTP_201_CREATED)
async def import_material(
    user: CurrentUser,
    session: SessionDep,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    module: str = Form(..., min_length=1, description="модуль-владелец материала"),
) -> dict:
    """Загрузить PDF или Markdown: текст извлекается и режется на фрагменты."""
    # Читаем с запасом в один байт: так превышение лимита видно без чтения всего файла.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ",
        )
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Файл пустой")
    try:
        result = extract(file.filename or "", data)
    except UnsupportedFile as e:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from e
    except NothingToExtract as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    material = Material(
        user_id=user.id,
        module=module,
        source=result.source,
        title=(title or "").strip() or result.title,
        content={
            "filename": file.filename,
            "pages": result.pages,
            "fragments": [f.dump() for f in result.fragments],
        },
    )
    session.add(material)
    session.commit()
    session.refresh(material)
    return _summary(material)


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_material(material_id: str, user: CurrentUser, session: SessionDep) -> None:
    """Удалить можно только свой материал; общие (user_id = null) не трогаются."""
    try:
        mid = uuid.UUID(material_id)
    except ValueError:
        return
    m = session.get(Material, mid)
    if m is not None and m.user_id == user.id:
        session.delete(m)
        session.commit()
