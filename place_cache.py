"""Durable shared city places, separate from each project's editable copies.

Project writes enqueue inside their own transaction. ``flush`` runs only after
that transaction commits, so temporary cache failures cannot lose the write.
Deleting a project item deliberately does not delete its shared cache entry.
"""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from city_catalog import find_catalog_city, normalize_city_name
from place_taxonomy import normalize_place


CACHE_FILE_NAME = "place_cache.db"
CACHE_FIELDS = {
    "attraction": (
        "name", "district", "category", "description", "duration", "transport",
        "navigation_link", "link", "scenic_rating", "tags",
    ),
    "food": ("name", "category", "description", "where_to_try", "tip", "link", "cuisine", "tags"),
}


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 5000")
    try:
        with db:
            yield db
    finally:
        db.close()


def cache_path(db_path: Path) -> Path:
    """All project databases in a deployment directory share one cache."""
    return Path(db_path).resolve().with_name(CACHE_FILE_NAME)


def canonical_city_name(name: str) -> str:
    match = find_catalog_city(name)
    return match["name"] if match else unicodedata.normalize("NFKC", name).strip()


def place_name_key(name: str) -> str:
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


def _business_payload(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {field: payload.get(field, "") for field in CACHE_FIELDS[kind] if field != "tags"}
    if not all(isinstance(value, str) for value in result.values()):
        raise ValueError("缓存景点、美食的内容必须是文字。")
    result = {field: value.strip() for field, value in result.items()}
    if not result["name"]:
        raise ValueError("缓存景点、美食需要名称。")
    return normalize_place(kind, dict(result, tags=payload.get("tags", [])), legacy=True)


def enqueue(
    db: sqlite3.Connection,
    city_name: str,
    kind: str,
    payload: Mapping[str, Any],
    updated_at: str,
) -> bool:
    """Add a cache event to the caller's transaction; never commit here."""
    if kind not in CACHE_FIELDS:
        return False
    city_name = canonical_city_name(city_name)
    if not city_name:
        raise ValueError("缓存景点、美食需要城市名称。")
    cleaned = _business_payload(kind, payload)
    db.execute(
        """INSERT INTO cache_outbox(
            city_name, city_key, kind, name_key, payload, updated_at, queued_at_ns
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            city_name, normalize_city_name(city_name), kind,
            place_name_key(cleaned["name"]),
            json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")),
            updated_at, time.time_ns(),
        ),
    )
    return True


def prepare(db_path: Path, *, mark_existing_cities: bool = True) -> int:
    """Create queue tables and backfill this project's existing places once.

Existing projects mark their current cities as already hydrated, preventing
deleted items from reappearing. New empty projects pass False and hydrate each
city from the shared cache when the user first selects that city.
"""
    with _connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS cache_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city_name TEXT NOT NULL,
            city_key TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('attraction', 'food')),
            name_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            queued_at_ns INTEGER NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS city_cache_imports (
            city_id TEXT PRIMARY KEY,
            imported_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS place_cache_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
        backfilled = db.execute(
            "SELECT 1 FROM place_cache_state WHERE key = 'backfilled_v1'"
        ).fetchone()
        if not backfilled:
            rows = db.execute("""SELECT cities.name AS city_name, items.kind,
                items.payload, items.updated_at
                FROM items JOIN cities ON cities.id = items.city_id
                WHERE items.kind IN ('attraction', 'food')
                ORDER BY items.updated_at, items.id""").fetchall()
            for row in rows:
                enqueue(db, row["city_name"], row["kind"], json.loads(row["payload"]), row["updated_at"])
            if mark_existing_cities:
                now = datetime.now(timezone.utc).isoformat()
                db.execute("""INSERT OR IGNORE INTO city_cache_imports(city_id, imported_at)
                    SELECT id, ? FROM cities""", (now,))
            db.execute("INSERT INTO place_cache_state VALUES ('backfilled_v1', '1')")
    return flush(db_path)


def flush(db_path: Path) -> int:
    """Idempotently copy committed events to the cache, leaving failures queued.

The cache commit happens before acknowledging project events. If interrupted
between commits, replaying those events produces the same cache state.
"""
    with _connect(db_path) as project_db:
        project_db.execute("BEGIN IMMEDIATE")
        rows = project_db.execute("SELECT * FROM cache_outbox ORDER BY id").fetchall()
        if not rows:
            return 0
        with _connect(cache_path(db_path)) as shared_db:
            shared_db.execute("""CREATE TABLE IF NOT EXISTS places (
                city_key TEXT NOT NULL,
                city_name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('attraction', 'food')),
                name_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                queued_at_ns INTEGER NOT NULL,
                PRIMARY KEY(city_key, kind, name_key)
            )""")
            shared_db.executemany("""INSERT INTO places(
                    city_key, city_name, kind, name_key, payload, updated_at, queued_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(city_key, kind, name_key) DO UPDATE SET
                    city_name = excluded.city_name,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at,
                    queued_at_ns = excluded.queued_at_ns
                WHERE excluded.updated_at > places.updated_at OR
                    (excluded.updated_at = places.updated_at AND
                        excluded.queued_at_ns > places.queued_at_ns)""",
                [tuple(row[field] for field in (
                    "city_key", "city_name", "kind", "name_key", "payload", "updated_at", "queued_at_ns"
                )) for row in rows],
            )
        # Do not erase events queued after the selected batch. The write lock
        # already serializes this, and explicit IDs preserve that guarantee.
        project_db.executemany("DELETE FROM cache_outbox WHERE id = ?", [(row["id"],) for row in rows])
        return len(rows)


def load_city(db_path: Path, city_name: str) -> list[dict[str, Any]]:
    """Return business data ready for copying into one project's selected city."""
    flush(db_path)
    shared_path = cache_path(db_path)
    if not shared_path.exists():
        return []
    with _connect(shared_path) as shared_db:
        if not shared_db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'places'").fetchone():
            return []
        rows = shared_db.execute(
            "SELECT kind, payload FROM places WHERE city_key = ? ORDER BY kind, name_key",
            (normalize_city_name(canonical_city_name(city_name)),),
        ).fetchall()
    return [{"kind": row["kind"], "payload": _business_payload(row["kind"], json.loads(row["payload"]))} for row in rows]
