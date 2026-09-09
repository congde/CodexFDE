"""Local inventory file delivery; publish only a completely written CSV."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import uuid

from .identity import SYSTEM_PRINCIPAL
from .import_export import ImportExportService
from .store import ERPStore


def export_inventory_file(store: ERPStore, target_path: str | Path) -> Path:
    """Export the local course ledger without changing stock or the old report.

    This local function uses the course system principal. Network requests must
    continue to use their authenticated principal through ImportExportService.
    Write errors propagate to the caller; the old destination is replaced only
    after the temporary sibling has been flushed and closed.
    """
    target = Path(target_path).absolute()
    if target.resolve() == Path(store.path).resolve():
        raise ValueError("CSV 输出不能覆盖数据库")
    if target.is_symlink():
        raise ValueError("导出目标不能是符号链接")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"导出目录不存在：{target.parent}")
    if target.exists() and not target.is_file():
        raise ValueError("导出目标必须是文件")
    content = ImportExportService(store).export_csv(SYSTEM_PRINCIPAL, "inventory")
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="从本地练习数据库导出完整库存 CSV，失败时保留旧目标")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.database.is_file():
            raise FileNotFoundError(f"练习数据库不存在：{args.database}")
        if args.database.resolve() == args.output.resolve():
            raise ValueError("CSV 输出不能覆盖数据库")
        result = export_inventory_file(ERPStore(args.database), args.output)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "exported", "path": str(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
