"""Резервная копия объектного хранилища источников (T-0073): копия и проверка.

    uv run python -m scripts.backup_objects [каталог]          # снять копию (по умолчанию .data/backups)
    uv run python -m scripts.backup_objects --verify [каталог]  # сверить последнюю копию с хранилищем

Копия — каталог `objects-ГГГГММДД-ЧЧММСС` с файлами и `manifest.json` (ключ, размер, sha256).
Копия, которую не сверили, копией не считается: `--verify` читает каждый файл из хранилища
и сравнивает хэш с манифестом.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from core.config import settings
from core.objects import S3ObjectStore, get_store


def _keys() -> list[str]:
    store = get_store()
    if not isinstance(store, S3ObjectStore):
        raise SystemExit("S3_ENDPOINT не задан: копировать нечего (хранилище в памяти)")
    out: list[str] = []
    pager = store._s3.get_paginator("list_objects_v2")  # noqa: SLF001 — скрипт знает реализацию
    for page in pager.paginate(Bucket=settings.s3_bucket):
        out += [item["Key"] for item in page.get("Contents", [])]
    return out


def backup(root: Path) -> Path:
    store = get_store()
    target = root / f"objects-{datetime.now():%Y%m%d-%H%M%S}"
    manifest = []
    for key in _keys():
        data = store.get(key)
        path = target / "files" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        manifest.append({"key": key, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    (target / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return target


def verify(root: Path) -> int:
    copies = sorted(root.glob("objects-*"))
    if not copies:
        raise SystemExit(f"Нет копий в {root}")
    latest = copies[-1]
    manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
    store = get_store()
    bad = []
    for item in manifest:
        file = latest / "files" / item["key"]
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]:
            bad.append(f"{item['key']}: файл копии повреждён")
        elif not store.exists(item["key"]):
            bad.append(f"{item['key']}: нет в хранилище")
    for line in bad:
        print(line, file=sys.stderr)
    print(f"Копия {latest.name}: файлов {len(manifest)}, расхождений {len(bad)}")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    verify_only = "--verify" in argv
    args = [a for a in argv if not a.startswith("--")]
    root = Path(args[0]) if args else Path(".data/backups")
    root.mkdir(parents=True, exist_ok=True)
    if verify_only:
        return verify(root)
    target = backup(root)
    print(target)
    return verify(root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
