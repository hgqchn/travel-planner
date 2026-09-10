#!/usr/bin/env python3
"""Back up all registered projects and their shared place cache without downtime."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import server


def project_filenames(snapshot_path: Path) -> list[str]:
    """Read the project collection from the already completed main snapshot."""
    with sqlite3.connect(snapshot_path) as db:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_registry'"
        ).fetchone() is None:
            return []
        rows = db.execute("SELECT id, filename FROM project_registry ORDER BY id").fetchall()
    seen = set()
    filenames = []
    for project_id, filename in rows:
        if (not isinstance(project_id, str)
                or not re.fullmatch(r"main|[0-9a-f]{16}", project_id)
                or project_id in seen):
            raise ValueError("项目注册表中的项目 ID 无效或重复。")
        seen.add(project_id)
        expected = "trip.db" if project_id == "main" else f"project-{project_id}.db"
        if filename != expected:
            raise ValueError("项目注册表中的数据库文件名无效。")
        if project_id != "main":
            filenames.append(filename)
    if "main" not in seen:
        raise ValueError("项目注册表缺少默认项目。")
    return filenames


def backup_all(data_dir: Path, output: Path) -> Path:
    """Publish a directory only after each independent SQLite snapshot succeeds."""
    data_dir = data_dir.expanduser().resolve()
    output = output.expanduser().absolute()
    # Resolve the parent while preserving a possible leaf symlink for rejection.
    output = output.parent.resolve() / output.name
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"备份目标已存在：{output}")
    if not (data_dir / "trip.db").is_file():
        raise FileNotFoundError(f"源数据库不存在：{data_dir / 'trip.db'}")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Serialize attempts to publish the same output without locking source databases.
    lock_path = output.with_name(f".{output.name}.backup.lock")
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    temporary = None
    try:
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"备份目标已存在：{output}")
        temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.incomplete-", dir=output.parent))
        main_snapshot = server.create_backup(data_dir / "trip.db", temporary / "trip.db")
        for filename in project_filenames(main_snapshot):
            server.create_backup(data_dir / filename, temporary / filename)
        cache_path = data_dir / "place_cache.db"
        if cache_path.exists() or cache_path.is_symlink():
            server.create_backup(cache_path, temporary / "place_cache.db")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"备份目标已存在：{output}")
        temporary.rename(output)
        temporary = None
        return output
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
        os.close(lock_fd)
        lock_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="在线备份全部旅行项目和共享景点美食缓存")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("TRIP_DATA_DIR", server.DEFAULT_DATA_DIR)))
    parser.add_argument("--output", type=Path, required=True, help="新的完整备份目录，不能已存在")
    args = parser.parse_args()
    try:
        destination = backup_all(args.data_dir, args.output)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"备份失败：{error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(destination)


if __name__ == "__main__":
    main()
