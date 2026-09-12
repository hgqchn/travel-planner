#!/usr/bin/env python3
"""A tiny multi-city collaborative trip planner using only Python's standard library."""

from __future__ import annotations

import argparse
import collections
import hashlib
import hmac
import http.cookies
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import uuid
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from city_catalog import (
    CATALOG_MIGRATION_KEY,
    CATALOG_EXPANSION_KEY,
    LEGACY_CITY_IDS,
    city_metadata,
    city_metadata_version,
    find_city_region,
    find_catalog_city,
    load_catalog,
    normalize_city_name,
)
from metro_maps import MetroMapError, MetroMapStore
from amap_service import AMapError, AMapService
from scenic_catalog import RATINGS, enrich_attraction, get_catalog as get_scenic_catalog, rated_payload
from itinerary_export import MIME_TYPES as EXPORT_MIME_TYPES, build_docx, build_xlsx, filename as export_filename
import itinerary_links
from itinerary_image import build_image_data
import project_itinerary
import daily_planner
import daily_plan_store
import travel_guidance
from place_taxonomy import TAXONOMY, normalize_place, migrate_project
from project_store import ProjectStore


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PUBLIC_DIR = PROJECT_ROOT / "public"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
SEED_PATH = PROJECT_ROOT / "seed_data.json"
MAX_BODY_BYTES = 64 * 1024
MAX_AI_BODY_BYTES = 8 * 1024 * 1024
MAX_ITEMS_PER_KIND = 250
MAX_ITEMS_TOTAL = int(os.environ.get("TRIP_MAX_ITEMS_TOTAL", "50000"))
MAX_USERS = 1000
MAX_CITIES = 500
MAX_SESSIONS_PER_USER = 20
PROJECT_ACCESS_MAX_AGE = 30 * 24 * 60 * 60
ADMIN_SESSION_MAX_AGE = 12 * 60 * 60
SECRET_HASH_ITERATIONS = 240_000
SCHEMA_VERSION = 3
DEFAULT_CITY_ID = "shanghai"
KINDS = ("attraction", "transit", "food", "itinerary")
USER_ID_PATTERN = re.compile(r"^[\w.-]{2,20}$", re.UNICODE)
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

FIELD_RULES: dict[str, dict[str, tuple[int, bool]]] = {
    "attraction": {
        "name": (60, True),
        "district": (30, False),
        "category": (30, False),
        "scenic_rating": (2, False),
        "description": (600, False),
        "duration": (40, False),
        "transport": (160, False),
        "navigation_link": (500, False),
        "link": (500, False),
    },
    "transit": {
        "name": (60, True),
        "color": (7, True),
        "route": (180, False),
        "connections": (220, False),
        "service_note": (220, False),
        "link": (500, False),
    },
    "food": {
        "name": (60, True),
        "category": (30, False),
        "cuisine": (40, False),
        "description": (500, False),
        "where_to_try": (120, False),
        "tip": (220, False),
        "link": (500, False),
    },
    "itinerary": {
        "date": (10, True),
        "start_time": (5, False),
        "title": (80, True),
        "category": (30, False),
        "location": (120, False),
        "notes": (600, False),
        "link": (500, False),
    },
}


class ApiError(Exception):
    def __init__(self, status: int, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_project_code(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty) or len(value) > 200:
        raise ValueError("项目口令不能为空，最多 200 个字符，可自由设置。")
    return value


def hash_secret(value: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        bytes.fromhex(salt),
        SECRET_HASH_ITERATIONS,
    ).hex()


def create_secret_record(value: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    return salt, hash_secret(value, salt)


def verify_secret(value: Any, salt: str, expected_hash: str) -> bool:
    if not isinstance(value, str) or len(value) > 200:
        return False
    try:
        supplied_hash = hash_secret(value, salt)
    except ValueError:
        return False
    return hmac.compare_digest(expected_hash, supplied_hash)


def create_signed_token(secret: str, scope: str, version: int) -> str:
    issued_at = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    unsigned = f"{scope}.{version}.{issued_at}.{nonce}"
    signature = hmac.new(
        secret.encode("utf-8"), unsigned.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{unsigned}.{signature}"


def validate_signed_token(
    token: str,
    secret: str,
    scope: str,
    version: int,
    max_age: int,
) -> bool:
    if not token or len(token) > 400:
        return False
    parts = token.split(".")
    if len(parts) != 5:
        return False
    supplied_scope, supplied_version, issued_at_text, nonce, supplied_signature = parts
    if (
        supplied_scope != scope
        or supplied_version != str(version)
        or not issued_at_text.isdigit()
        or not nonce
        or not supplied_signature
    ):
        return False
    issued_at = int(issued_at_text)
    now = int(time.time())
    if issued_at > now + 300 or now - issued_at > max_age:
        return False
    unsigned = f"{supplied_scope}.{supplied_version}.{issued_at_text}.{nonce}"
    expected_signature = hmac.new(
        secret.encode("utf-8"), unsigned.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, supplied_signature)


def create_project_access_token(session_secret: str, access_version: int) -> str:
    return create_signed_token(session_secret, "project", access_version)


def validate_project_access_token(
    token: str, session_secret: str, access_version: int
) -> bool:
    return validate_signed_token(
        token,
        session_secret,
        "project",
        access_version,
        PROJECT_ACCESS_MAX_AGE,
    )


def create_admin_access_token(admin_password: str) -> str:
    return create_signed_token(admin_password, "admin", 1)


def validate_admin_access_token(token: str, admin_password: str) -> bool:
    return validate_signed_token(
        token,
        admin_password,
        "admin",
        1,
        ADMIN_SESSION_MAX_AGE,
    )


def connect_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


SCHEMA_V3_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY COLLATE NOCASE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL COLLATE NOCASE REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id
ON user_sessions(user_id);

CREATE TABLE IF NOT EXISTS cities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    city_id TEXT NOT NULL REFERENCES cities(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('attraction', 'transit', 'food', 'itinerary')),
    position INTEGER NOT NULL,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_city_kind_position
ON items(city_id, kind, position);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision INTEGER NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
    item_id TEXT NOT NULL,
    city_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    item_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    happened_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_revision
ON activity(revision DESC);

CREATE INDEX IF NOT EXISTS idx_activity_city_revision
ON activity(city_id, revision DESC);

CREATE TABLE IF NOT EXISTS project_settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    project_name TEXT NOT NULL,
    code_salt TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    session_secret TEXT NOT NULL,
    access_version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

INSERT OR IGNORE INTO meta(key, value) VALUES ('revision', 0);
INSERT OR IGNORE INTO meta(key, value)
SELECT 'seeded', CASE WHEN EXISTS(SELECT 1 FROM items) THEN 1 ELSE 0 END;
"""


def migrate_v1_to_v2(db: sqlite3.Connection) -> None:
    """Preserve v1 users, sessions, content and activity while widening the data model."""
    db.executescript(
        """
        BEGIN IMMEDIATE;

        ALTER TABLE users RENAME TO users_v1;
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY COLLATE NOCASE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE user_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL COLLATE NOCASE REFERENCES users(user_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        INSERT INTO users(user_id, created_at, last_seen_at)
        SELECT user_id, created_at, last_seen_at FROM users_v1;
        INSERT INTO user_sessions(token_hash, user_id, created_at, last_seen_at)
        SELECT token_hash, user_id, created_at, last_seen_at FROM users_v1;
        DROP TABLE users_v1;
        CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);

        ALTER TABLE items RENAME TO items_v1;
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('attraction', 'transit', 'food', 'itinerary')),
            position INTEGER NOT NULL,
            payload TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL
        );
        INSERT INTO items(
            id, kind, position, payload, version, created_at,
            updated_at, created_by, updated_by
        )
        SELECT
            id, kind, position, payload, version, created_at,
            updated_at, created_by, updated_by
        FROM items_v1;
        DROP TABLE items_v1;
        CREATE INDEX idx_items_kind_position ON items(kind, position);

        PRAGMA user_version = 2;
        COMMIT;
        """
    )


def migrate_v2_to_v3(db: sqlite3.Connection, initial_project_code: str) -> None:
    """Add project administration and city ownership without losing v2 data."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL UNIQUE,
            action TEXT NOT NULL, item_id TEXT NOT NULL, kind TEXT NOT NULL,
            item_name TEXT NOT NULL, user_id TEXT NOT NULL, happened_at TEXT NOT NULL
        );
    """)
    timestamp = utc_now()
    code_salt, code_hash_value = create_secret_record(initial_project_code)
    session_secret = secrets.token_urlsafe(32)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            CREATE TABLE cities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                position INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO cities(id, name, position, created_at, updated_at)
            VALUES (?, '上海', 1, ?, ?)
            """,
            (DEFAULT_CITY_ID, timestamp, timestamp),
        )
        db.execute("ALTER TABLE items RENAME TO items_v2")
        db.execute(
            """
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                city_id TEXT NOT NULL REFERENCES cities(id) ON DELETE RESTRICT,
                kind TEXT NOT NULL CHECK (kind IN ('attraction', 'transit', 'food', 'itinerary')),
                position INTEGER NOT NULL,
                payload TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_by TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO items(
                id, city_id, kind, position, payload, version, created_at,
                updated_at, created_by, updated_by
            )
            SELECT
                id, ?, kind, position, payload, version, created_at,
                updated_at, created_by, updated_by
            FROM items_v2
            """,
            (DEFAULT_CITY_ID,),
        )
        db.execute("DROP TABLE items_v2")
        db.execute(
            "CREATE INDEX idx_items_city_kind_position ON items(city_id, kind, position)"
        )

        db.execute("ALTER TABLE activity RENAME TO activity_v2")
        db.execute(
            """
            CREATE TABLE activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                revision INTEGER NOT NULL UNIQUE,
                action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
                item_id TEXT NOT NULL,
                city_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                item_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                happened_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO activity(
                id, revision, action, item_id, city_id, kind,
                item_name, user_id, happened_at
            )
            SELECT
                id, revision, action, item_id, ?, kind,
                item_name, user_id, happened_at
            FROM activity_v2
            """,
            (DEFAULT_CITY_ID,),
        )
        db.execute("DROP TABLE activity_v2")
        db.execute("CREATE INDEX idx_activity_revision ON activity(revision DESC)")
        db.execute(
            """
            CREATE TABLE project_settings (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                project_name TEXT NOT NULL,
                code_salt TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                session_secret TEXT NOT NULL,
                access_version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO project_settings(
                singleton, project_name, code_salt, code_hash,
                session_secret, access_version, updated_at
            ) VALUES (1, '我的旅游规划', ?, ?, ?, 1, ?)
            """,
            (code_salt, code_hash_value, session_secret, timestamp),
        )
        db.execute("PRAGMA user_version = 3")
        db.commit()
    except Exception:
        db.rollback()
        raise


def ensure_project_records(db: sqlite3.Connection, initial_project_code: str) -> None:
    timestamp = utc_now()
    db.execute(
        """
        INSERT INTO cities(id, name, position, created_at, updated_at)
        SELECT ?, '上海', 1, ?, ? WHERE NOT EXISTS (SELECT 1 FROM cities)
        """,
        (DEFAULT_CITY_ID, timestamp, timestamp),
    )
    if not db.execute("SELECT 1 FROM project_settings WHERE singleton = 1").fetchone():
        code_salt, code_hash_value = create_secret_record(initial_project_code)
        db.execute(
            """
            INSERT INTO project_settings(
                singleton, project_name, code_salt, code_hash,
                session_secret, access_version, updated_at
            ) VALUES (1, '我的旅游规划', ?, ?, ?, 1, ?)
            """,
            (code_salt, code_hash_value, secrets.token_urlsafe(32), timestamp),
        )


def apply_city_catalog_upgrade(db: sqlite3.Connection, *, fresh: bool = False,
                               migration_key: str = CATALOG_MIGRATION_KEY,
                               excluded_ids: frozenset = frozenset()) -> None:
    """Append missing defaults once, preserving existing IDs, names and content."""
    if db.execute("SELECT 1 FROM meta WHERE key = ?", (migration_key,)).fetchone():
        return
    existing = db.execute("SELECT id, name FROM cities").fetchall()
    existing_ids = {row["id"] for row in existing}
    represented = {
        match["id"]
        for row in existing
        for match in [find_catalog_city(row["name"])]
        if match is not None
    }
    position = db.execute("SELECT COALESCE(MAX(position), 0) FROM cities").fetchone()[0]
    timestamp = utc_now()
    added = 0
    for candidate in load_catalog():
        if candidate["id"] in represented or candidate["id"] in excluded_ids or len(existing_ids) >= MAX_CITIES:
            continue
        city_id = candidate["id"]
        while city_id in existing_ids:
            city_id = uuid.uuid4().hex[:12]
        position += 1
        db.execute(
            "INSERT INTO cities(id, name, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (city_id, candidate["name"], position, timestamp, timestamp),
        )
        existing_ids.add(city_id)
        added += 1
    db.execute("INSERT INTO meta(key, value) VALUES (?, 1)", (migration_key,))
    if added and not fresh:
        db.execute("UPDATE meta SET value = value + 1 WHERE key = 'revision'")


def xiaohongshu_search_url(keyword: str) -> str:
    return (
        "https://www.xiaohongshu.com/search_result?keyword="
        f"{quote(keyword)}&type=51"
    )


def apply_v3_content_upgrade(
    db: sqlite3.Connection, seed: dict[str, Any]
) -> bool:
    """Move Shanghai guide links to Xiaohongshu and append new bundled entries once."""
    changed = False
    timestamp = utc_now()
    for kind in ("attraction", "food"):
        rows = db.execute(
            "SELECT id, payload, version FROM items WHERE city_id = ? AND kind = ?",
            (DEFAULT_CITY_ID, kind),
        ).fetchall()
        existing_names: set[str] = set()
        for row in rows:
            payload = json.loads(row["payload"])
            name = str(payload.get("name", "")).strip()
            if name:
                existing_names.add(name.casefold())
                new_link = xiaohongshu_search_url("上海 " + name)
                if payload.get("link") != new_link:
                    payload["link"] = new_link
                    db.execute(
                        """
                        UPDATE items
                        SET payload = ?, version = ?, updated_at = ?, updated_by = 'system'
                        WHERE id = ?
                        """,
                        (
                            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                            int(row["version"]) + 1,
                            timestamp,
                            row["id"],
                        ),
                    )
                    changed = True

        position = db.execute(
            """
            SELECT COALESCE(MAX(position), 0)
            FROM items WHERE city_id = ? AND kind = ?
            """,
            (DEFAULT_CITY_ID, kind),
        ).fetchone()[0]
        for candidate in seed.get(kind, []):
            validated = validate_payload(kind, normalize_place(kind, candidate, legacy=True))
            name = validated["name"]
            if name.casefold() in existing_names:
                continue
            position += 1
            validated["link"] = xiaohongshu_search_url("上海 " + name)
            db.execute(
                """
                INSERT INTO items(
                    id, city_id, kind, position, payload, version, created_at,
                    updated_at, created_by, updated_by
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'system', 'system')
                """,
                (
                    uuid.uuid4().hex[:12],
                    DEFAULT_CITY_ID,
                    kind,
                    position,
                    json.dumps(validated, ensure_ascii=False, separators=(",", ":")),
                    timestamp,
                    timestamp,
                ),
            )
            existing_names.add(name.casefold())
            changed = True
    if changed:
        db.execute("UPDATE meta SET value = value + 1 WHERE key = 'revision'")
    return changed


def init_database(
    db_path: Path,
    initial_project_code: str,
    seed_path: Path = SEED_PATH,
    *,
    empty_project: bool = False,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_db(db_path) as db:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库版本 {version} 高于当前程序支持的版本 {SCHEMA_VERSION}，拒绝启动。"
            )
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
        if version == 1:
            migrate_v1_to_v2(db)
            version = 2
        upgraded_to_v3 = version == 2
        if version == 2:
            migrate_v2_to_v3(db, initial_project_code)
            version = 3
        db.executescript(SCHEMA_V3_SQL)
        itinerary_links.ensure_schema(db)
        travel_guidance.ensure_schema(db)
        ensure_project_records(db, initial_project_code)
        apply_city_catalog_upgrade(db, fresh=version == 0)
        if version == 0:
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        seeded = db.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()["value"]
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        if seed_path == SEED_PATH:
            extra = json.loads((PROJECT_ROOT / "shanghai_extra.json").read_text(encoding="utf-8"))
            for kind in ("attraction", "food"):
                seed[kind].extend(extra[kind])
                for entry in seed[kind]:
                    entry["link"] = xiaohongshu_search_url("上海 " + entry["name"])
        if seeded == 0:
            timestamp = utc_now()
            for kind in (() if empty_project else KINDS):
                for position, payload in enumerate(seed.get(kind, []), start=1):
                    validated = validate_payload(kind, normalize_place(kind, payload, legacy=True))
                    db.execute(
                        """
                        INSERT INTO items(
                            id, city_id, kind, position, payload, version, created_at,
                            updated_at, created_by, updated_by
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'system', 'system')
                        """,
                        (
                            uuid.uuid4().hex[:12],
                            DEFAULT_CITY_ID,
                            kind,
                            position,
                            json.dumps(validated, ensure_ascii=False, separators=(",", ":")),
                            timestamp,
                            timestamp,
                        ),
                    )
            db.execute("UPDATE meta SET value = 1 WHERE key = 'seeded'")
        elif upgraded_to_v3:
            apply_v3_content_upgrade(db, extra if seed_path == SEED_PATH else seed)
        migrate_project(db, utc_now())
        daily_plan_store.ensure_schema(db)
        db.execute("PRAGMA optimize")
    import place_cache
    place_cache.prepare(db_path, mark_existing_cities=not empty_project)
    apply_city_catalog_expansion(db_path)
    backfill_itinerary_links(db_path)
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if travel_guidance.recover_imports(db):
            next_revision(db)


def apply_city_catalog_expansion(db_path: Path) -> None:
    """Append nationwide defaults once; never prune or rewrite existing content."""
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        apply_city_catalog_upgrade(db, migration_key=CATALOG_EXPANSION_KEY, excluded_ids=LEGACY_CITY_IDS)


def queue_cached_place(db: sqlite3.Connection, city_id: str, kind: str, payload: dict, timestamp: str) -> None:
    if kind not in {'attraction', 'food'}:
        return
    import place_cache
    city = db.execute('SELECT name FROM cities WHERE id=?', (city_id,)).fetchone()
    if city:
        place_cache.enqueue(db, city['name'], kind, payload, timestamp)


def flush_place_cache(db_path: Path) -> None:
    import place_cache
    try:
        place_cache.flush(db_path)
    except (OSError, sqlite3.Error):
        # The durable outbox is committed with the item and retried on later reads.
        print('城市资料缓存暂时不可用，待同步内容已保留。', file=sys.stderr)


def hydrate_city_cache(db_path: Path, city_id: str) -> None:
    """Copy cached places only once, so project deletions stay deleted."""
    import place_cache
    with connect_db(db_path) as db:
        city = db.execute('SELECT name FROM cities WHERE id=?', (city_id,)).fetchone()
        if not city or db.execute('SELECT 1 FROM city_cache_imports WHERE city_id=?', (city_id,)).fetchone():
            return
    cached = place_cache.load_city(db_path, city['name'])
    with connect_db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT name FROM cities WHERE id=?', (city_id,)).fetchone()
        if not current or current['name'] != city['name'] or db.execute('SELECT 1 FROM city_cache_imports WHERE city_id=?', (city_id,)).fetchone():
            return
        seen = {(row['kind'], ''.join(unicodedata.normalize('NFKC', json.loads(row['payload'])['name']).casefold().split())) for row in db.execute("SELECT kind,payload FROM items WHERE city_id=? AND kind IN ('attraction','food')", (city_id,))}
        counts = {kind: db.execute('SELECT COUNT(*) FROM items WHERE city_id=? AND kind=?', (city_id,kind)).fetchone()[0] for kind in ('attraction','food')}
        total_count = db.execute('SELECT COUNT(*) FROM items').fetchone()[0]
        omitted = {'attraction': 0, 'food': 0}
        timestamp = utc_now()
        for entry in cached:
            kind, payload = entry['kind'], validate_payload(entry['kind'], entry['payload'])
            payload = rated_payload(kind, payload, current['name'])
            signature = (kind, ''.join(unicodedata.normalize('NFKC', payload['name']).casefold().split()))
            if signature in seen:
                continue
            if counts[kind] >= MAX_ITEMS_PER_KIND or total_count >= MAX_ITEMS_TOTAL:
                omitted[kind] += 1
                continue
            ensure_item_capacity(db, city_id, kind)
            item_id = uuid.uuid4().hex[:12]
            position = db.execute('SELECT COALESCE(MAX(position),0)+1 FROM items WHERE city_id=? AND kind=?', (city_id,kind)).fetchone()[0]
            db.execute("INSERT INTO items VALUES(?,?,?,?,?,1,?,?,'城市资料库','城市资料库')", (item_id,city_id,kind,position,json.dumps(payload,ensure_ascii=False),timestamp,timestamp))
            revision = next_revision(db)
            db.execute("INSERT INTO activity(revision,action,item_id,city_id,kind,item_name,user_id,happened_at) VALUES(?,'create',?,?,?,?,'城市资料库',?)", (revision,item_id,city_id,kind,payload['name'],timestamp))
            seen.add(signature)
            counts[kind] += 1
            total_count += 1
        for kind, count in omitted.items():
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (f'cache_omitted:{city_id}:{kind}', count))
        db.execute('INSERT INTO city_cache_imports VALUES(?,?)', (city_id,timestamp))


def validate_user_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(HTTPStatus.BAD_REQUEST, "请输入用户 ID。")
    user_id = value.strip()
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "用户 ID 需为 2–20 位中文、字母、数字、点、横线或下划线。")
    return user_id


def validate_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ApiError(HTTPStatus.BAD_REQUEST, "外部链接必须是完整的 http:// 或 https:// 地址。")
    if parsed.username or parsed.password:
        raise ApiError(HTTPStatus.BAD_REQUEST, "外部链接不能包含用户名或密码。")
    return value


def validate_payload(kind: str, value: Any) -> dict[str, Any]:
    if kind not in KINDS:
        raise ApiError(HTTPStatus.NOT_FOUND, "未知内容类型。")
    if not isinstance(value, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "内容格式不正确。")
    cleaned: dict[str, Any] = {}
    for field, (limit, required) in FIELD_RULES[kind].items():
        raw = value.get(field, "")
        if not isinstance(raw, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} 必须是文字。")
        item = raw.strip()
        if required and not item:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请填写必填项。")
        if len(item) > limit:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} 最多 {limit} 个字符。")
        if field in {"link", "navigation_link"}:
            item = validate_url(item)
        if field == "scenic_rating" and item not in RATINGS:
            raise ApiError(HTTPStatus.BAD_REQUEST, "景区级别请选择 4A、5A 或留空自动匹配。")
        if field == "color" and not HEX_COLOR_PATTERN.fullmatch(item):
            raise ApiError(HTTPStatus.BAD_REQUEST, "线路颜色必须为六位十六进制颜色。")
        if field == "date":
            if not DATE_PATTERN.fullmatch(item):
                raise ApiError(HTTPStatus.BAD_REQUEST, "行程日期格式必须为 YYYY-MM-DD。")
            try:
                datetime.strptime(item, "%Y-%m-%d")
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "请输入有效的行程日期。") from exc
        if field == "start_time" and item and not TIME_PATTERN.fullmatch(item):
            raise ApiError(HTTPStatus.BAD_REQUEST, "行程时间格式必须为 HH:MM。")
        cleaned[field] = item
    if kind == 'itinerary':
        try:
            cleaned['attraction_names'] = itinerary_links.normalize_attraction_names(value.get('attraction_names', []))
            cleaned.update({key: value[key] for key in daily_planner.VISIT_FIELDS if key in value})
            cleaned = daily_planner.normalize_visit(cleaned)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None
    if kind in ("attraction", "food"):
        try:
            cleaned = normalize_place(kind, dict(cleaned, tags=value.get("tags", [])))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from None
    return cleaned


def item_label(kind: str, payload: dict[str, Any]) -> str:
    return str(payload["title"] if kind == "itinerary" else payload["name"])


def row_to_item(row: sqlite3.Row, city: str | dict[str, Any] | None = None) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "city_id": row["city_id"],
        "kind": row["kind"],
        "position": row["position"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"],
        "updated_by": row["updated_by"],
        **normalize_place(row["kind"], json.loads(row["payload"]), legacy=True),
    }
    if row['kind'] == 'itinerary':
        item = daily_planner.normalize_visit(item)
    return enrich_attraction(item, city) if row['kind'] == 'attraction' else item


def item_with_links(db, row, city: str) -> dict[str, Any]:
    """Keep mutation responses consistent with the annotated snapshot API."""
    item = row_to_item(row, city)
    if row['kind'] == 'itinerary':
        item = daily_planner.normalize_visit(item)
    if row['kind'] not in ('itinerary', 'attraction'):
        return item
    links = [dict(link) for link in db.execute(
        "SELECT * FROM itinerary_attractions WHERE itinerary_id=? OR attraction_id=? ORDER BY position",
        (row['id'], row['id']))]
    grouped = {kind: [] for kind in KINDS}
    grouped[row['kind']].append(item)
    ids = {link['attraction_id'] if row['kind'] == 'itinerary' else link['itinerary_id'] for link in links}
    for related_id in ids:
        related = db.execute("SELECT * FROM items WHERE id=? AND city_id=?", (related_id, row['city_id'])).fetchone()
        if related:
            grouped[related['kind']].append(row_to_item(related, city))
    itinerary_links.annotate_items(grouped, links)
    return item


def snapshot(db_path: Path, city_id: str | None = None) -> dict[str, Any]:
    flush_place_cache(db_path)
    if city_id:
        hydrate_city_cache(db_path, city_id)
    with connect_db(db_path) as db:
        db.execute("BEGIN")
        revision = db.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()["value"]
        project = db.execute(
            "SELECT project_name FROM project_settings WHERE singleton = 1"
        ).fetchone()
        city_rows = db.execute(
            "SELECT id, name, position FROM cities ORDER BY position, name COLLATE NOCASE"
        ).fetchall()
        if city_id is not None and (
            not isinstance(city_id, str) or not any(row["id"] == city_id for row in city_rows)
        ):
            raise ApiError(HTTPStatus.NOT_FOUND, "城市已不存在，请重新选择城市。")
        city_filter = " WHERE city_id = ?" if city_id is not None else ""
        parameters = (city_id,) if city_id is not None else ()
        rows = db.execute(
            "SELECT * FROM items" + city_filter + " ORDER BY city_id, kind, position, created_at, id",
            parameters,
        ).fetchall()
        activity_rows = db.execute(
            """
            SELECT revision, action, item_id, city_id, kind, item_name, user_id, happened_at
            FROM activity
            """ + city_filter + " ORDER BY revision DESC LIMIT 30",
            parameters,
        ).fetchall()
        links = [dict(row) for row in db.execute(
            "SELECT l.* FROM itinerary_attractions l JOIN items i ON i.id=l.itinerary_id"
            + (" WHERE i.city_id=?" if city_id is not None else "")
            + " ORDER BY l.itinerary_id,l.position", parameters)]
        day_keys = {(row['city_id'],json.loads(row['payload'])['date']) for row in rows if row['kind']=='itinerary'}
        day_keys.update((r['city_id'],r['date']) for r in db.execute('SELECT city_id,date FROM day_plans'+(' WHERE city_id=?' if city_id else ''), parameters))
        daily_plans = []
        for day_city, day_date in sorted(day_keys):
            plan = daily_plan_store.read(db,day_city,day_date)
            summary = {k:v for k,v in plan.items() if k not in {'visits','blocks','routes'}}
            import route_image
            summary['route_preview'] = route_image.preview(plan)
            if summary.get('evaluation'):
                summary['evaluation'] = {k:v for k,v in summary['evaluation'].items() if k!='legs'}
            daily_plans.append(summary)
        omitted = {}
        guidance = travel_guidance.read(db, city_id)
        project_plan = project_itinerary.overview(
            db.execute("SELECT id, city_id, payload, version, position FROM items WHERE kind='itinerary'").fetchall(),
            {row['id']: row['name'] for row in city_rows},
        )
        for kind in ('attraction', 'food'):
            row = db.execute('SELECT value FROM meta WHERE key=?', (f'cache_omitted:{city_id}:{kind}',)).fetchone()
            omitted[kind] = int(row['value']) if row else 0
    grouped = {kind: [] for kind in KINDS}
    city_names = {row['id']: row['name'] for row in city_rows}
    for row in rows:
        grouped[row["kind"]].append(row_to_item(row, city_names.get(row['city_id'])))
    itinerary_links.annotate_items(grouped, links)
    return {
        "revision": revision,
        "project": {"name": project["project_name"]},
        "cities": [city_metadata(row) for row in city_rows],
        "city_metadata_version": city_metadata_version(),
        "city_id": city_id,
        "items": grouped,
        "activity": [dict(row) for row in activity_rows],
        "cache_info": {"omitted": omitted},
        "taxonomy": TAXONOMY,
        "travel_guidance": guidance,
        "project_itinerary": project_plan,
        "daily_plans": daily_plans,
        "scenic_catalog_version": get_scenic_catalog().fingerprint,
        "server_time": utc_now(),
    }


def guidance_state(db_path: Path, city_id: str) -> dict[str, Any]:
    with connect_db(db_path) as db:
        db.execute('BEGIN')
        groups = travel_guidance.states(db, city_id)
        if not groups:
            raise ApiError(404, '城市已不存在，请重新选择城市。')
        return groups[0]


def change_guidance(db_path: Path, data: Any, user_id: str, *, delete=False) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get('city_id'), str) or not data['city_id']:
        raise ApiError(400, '请选择有效的城市。')
    if not isinstance(data.get('version'), str) or not re.fullmatch(r'[a-f0-9]{24}', data['version']):
        raise ApiError(400, '提示版本无效，请重新打开编辑窗口。')
    summary, notices = '', []
    if not delete:
        notices = data.get('notices')
        if not isinstance(notices, list) or len(notices) > 12 or any(
                not isinstance(value, str) or len(value) > 500 for value in notices):
            raise ApiError(400, '最多填写 12 条提示，每条不超过 500 字。')
        notices = list(dict.fromkeys(value.strip() for value in notices if value.strip()))
        if not notices:
            raise ApiError(400, '请填写至少一条出行建议；不需要时可点击删除。')
    with connect_db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM users WHERE user_id=?', (user_id,)).fetchone():
            raise ApiError(401, '用户身份已失效，请重新选择 ID。')
        groups = travel_guidance.states(db, data['city_id'])
        if not groups:
            raise ApiError(404, '城市已不存在，请重新选择城市。')
        current = groups[0]
        if not delete and data.get('plan_version'):
            try:
                latest_plan = travel_guidance.planning_context(db, data['city_id'])['version']
            except ValueError:
                latest_plan = None
            if latest_plan != data['plan_version']:
                raise ApiError(409, '行程已变化，请确认最新行程后重新生成提示。')
        if current['version'] != data['version']:
            raise ApiError(409, '出行提示已更新，你的草稿已保留，请载入最新版后再修改。', {'current': current})
        group = travel_guidance.write(db, data['city_id'], summary, notices, user_id, utc_now(), deleted=delete)
        return {'guidance': group, 'revision': next_revision(db)}


def itinerary_export_snapshot(db_path: Path, city_id: str | None) -> dict[str, Any]:
    """Read just saved itineraries in one transaction, without hydrating caches."""
    with connect_db(db_path) as db:
        db.execute("BEGIN")
        project = db.execute("SELECT project_name FROM project_settings WHERE singleton=1").fetchone()
        cities = {row['id']: row['name'] for row in db.execute("SELECT id, name FROM cities")}
        if city_id is not None and city_id not in cities:
            raise ApiError(404, "城市已不存在，请重新选择城市。")
        rows = db.execute(
            "SELECT * FROM items WHERE kind='itinerary'" + (" AND city_id=?" if city_id is not None else ""),
            (city_id,) if city_id is not None else (),
        ).fetchall()
        # Export only travel content, never collaborator identities or credentials.
        items = [{**daily_planner.normalize_visit(json.loads(row['payload'])), 'city_name': cities[row['city_id']],
                  'position': row['position'], 'id': row['id']} for row in rows]
        export_days = [dict(daily_plan_store.read(db, cid, day),city_name=cities[cid]) for cid,day in sorted({(r['city_id'],json.loads(r['payload'])['date']) for r in rows})]
        itinerary_cities = {row['city_id'] for row in rows}
        guidance = [group for group in travel_guidance.read(db, city_id) if group['city_id'] in itinerary_cities]
    if not items:
        raise ApiError(400, "所选范围还没有已保存的行程，请先添加行程或导入 AI 规划结果。")
    items.sort(key=lambda item: (item['date'], item['city_name'], daily_planner.group_rank(item), item['position'], item['id']))
    return {'project_name': project['project_name'], 'scope_name': cities[city_id] if city_id is not None else '全部城市',
            'items': items, 'travel_guidance': guidance,
            'daily_plans': export_days,
            'exported_at': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S UTC+08:00')}


def list_users(db_path: Path) -> list[dict[str, str]]:
    with connect_db(db_path) as db:
        rows = db.execute(
            """
            SELECT user_id, created_at, last_seen_at
            FROM users
            ORDER BY last_seen_at DESC, user_id COLLATE NOCASE
            """
        ).fetchall()
    return [dict(row) for row in rows]


def validate_project_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(HTTPStatus.BAD_REQUEST, "请输入项目名称。")
    project_name = value.strip()
    if not project_name or len(project_name) > 60:
        raise ApiError(HTTPStatus.BAD_REQUEST, "项目名称需为 1–60 个字符。")
    return project_name


def validate_city_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(HTTPStatus.BAD_REQUEST, "请输入城市名称。")
    city_name = value.strip()
    if not city_name or len(city_name) > 30:
        raise ApiError(HTTPStatus.BAD_REQUEST, "城市名称需为 1–30 个字符。")
    return city_name


def find_existing_city(
    db: sqlite3.Connection, name: str, *, excluding_id: str | None = None
) -> sqlite3.Row | None:
    candidate = find_catalog_city(name)
    candidate_region = find_city_region(name)
    normalized = normalize_city_name(name)
    for row in db.execute("SELECT id, name, position FROM cities ORDER BY position"):
        if row["id"] == excluding_id:
            continue
        match = find_catalog_city(row["name"])
        region = find_city_region(row["name"]) if candidate_region else None
        if normalize_city_name(row["name"]) == normalized or (
            candidate and match and candidate["id"] == match["id"]
        ) or (
            candidate_region and region and candidate_region["code"] == region["code"]
        ):
            return row
    return None


def insert_city(db: sqlite3.Connection, name: str, now: str) -> dict[str, Any]:
    """Shared by regular-user and administrator creation, inside a write transaction."""
    existing = find_existing_city(db, name)
    if existing:
        raise ApiError(HTTPStatus.CONFLICT, "城市已存在，请选择已有城市。", {"city": city_metadata(existing)})
    if db.execute("SELECT COUNT(*) FROM cities").fetchone()[0] >= MAX_CITIES:
        raise ApiError(HTTPStatus.INSUFFICIENT_STORAGE, "城市数量已达上限，请先整理现有城市。")
    candidate = find_catalog_city(name)
    city_id = candidate["id"] if candidate else uuid.uuid4().hex[:12]
    while db.execute("SELECT 1 FROM cities WHERE id = ?", (city_id,)).fetchone():
        city_id = uuid.uuid4().hex[:12]
    name = candidate["name"] if candidate else name
    db.execute(
        "INSERT INTO cities(id, name, position, created_at, updated_at) "
        "VALUES (?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM cities), ?, ?)",
        (city_id, name, now, now),
    )
    row = db.execute("SELECT id, name, position FROM cities WHERE id = ?", (city_id,)).fetchone()
    return city_metadata(row)


def create_city(db_path: Path, data: Any, user_id: str) -> dict[str, Any]:
    """Add a city for an already authenticated project participant."""
    if not isinstance(data, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "内容格式不正确。")
    name = validate_city_name(data.get("name"))
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not isinstance(user_id, str) or not db.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone():
            raise ApiError(HTTPStatus.UNAUTHORIZED, "请先选择用户 ID。")
        city = insert_city(db, name, utc_now())
        revision = next_revision(db)
    return {"revision": revision, "city": city}


def project_auth_record(db_path: Path) -> sqlite3.Row:
    with connect_db(db_path) as db:
        row = db.execute(
            """
            SELECT code_salt, code_hash, session_secret, access_version
            FROM project_settings WHERE singleton = 1
            """
        ).fetchone()
    if not row:
        raise RuntimeError("项目配置不存在。")
    return row


def verify_project_code(db_path: Path, submitted_code: Any) -> bool:
    record = project_auth_record(db_path)
    return verify_secret(submitted_code, record["code_salt"], record["code_hash"])


def projects_matching_code(store: ProjectStore, code: str, exclude: str | None = None) -> list[dict]:
    """Call under the store lock so lookup and subsequent changes stay atomic."""
    return [project for project in store.list_projects()
            if project['id'] != exclude and verify_project_code(store.resolve(project['id']), code)]


def require_unique_project_code(store: ProjectStore, code: str, exclude: str | None = None) -> None:
    if code and projects_matching_code(store, code, exclude):
        raise ApiError(409, "该口令已用于其他项目，请使用不同的项目口令。")


def claim_identity(db_path: Path, data: Any, supplied_token: str = "") -> dict[str, str]:
    if not isinstance(data, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "内容格式不正确。")
    user_id = validate_user_id(data.get("user_id"))
    now = utc_now()
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT user_id FROM users WHERE user_id = ? COLLATE NOCASE",
            (user_id,),
        ).fetchone()
        if not row:
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= MAX_USERS:
                raise ApiError(503, "用户数量已达上限。")
            db.execute("INSERT INTO users VALUES (?, ?, ?)", (user_id, now, now))
            row = {"user_id": user_id}
        canonical_user_id = row["user_id"]

        if supplied_token:
            current = db.execute(
                "SELECT user_id FROM user_sessions WHERE token_hash = ?",
                (token_hash(supplied_token),),
            ).fetchone()
            if current and current["user_id"].casefold() == canonical_user_id.casefold():
                db.execute(
                    "UPDATE user_sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now, token_hash(supplied_token)),
                )
                db.execute(
                    "UPDATE users SET last_seen_at = ? WHERE user_id = ?",
                    (now, canonical_user_id),
                )
                return {"user_id": canonical_user_id, "token": supplied_token}

        new_token = secrets.token_urlsafe(32)
        db.execute(
            """
            INSERT INTO user_sessions(token_hash, user_id, created_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash(new_token), canonical_user_id, now, now),
        )
        db.execute(
            "UPDATE users SET last_seen_at = ? WHERE user_id = ?",
            (now, canonical_user_id),
        )
        db.execute(
            """
            DELETE FROM user_sessions
            WHERE user_id = ? COLLATE NOCASE
              AND token_hash NOT IN (
                  SELECT token_hash FROM user_sessions
                  WHERE user_id = ? COLLATE NOCASE
                  ORDER BY created_at DESC, rowid DESC
                  LIMIT ?
              )
            """,
            (canonical_user_id, canonical_user_id, MAX_SESSIONS_PER_USER),
        )
        return {"user_id": canonical_user_id, "token": new_token}


def authenticate(db_path: Path, token: str) -> str:
    if not token:
        raise ApiError(
            HTTPStatus.UNAUTHORIZED,
            "请先选择用户 ID。",
            {"code": "USER_REQUIRED"},
        )
    if len(token) < 20 or len(token) > 200:
        raise ApiError(
            HTTPStatus.UNAUTHORIZED,
            "身份凭证无效，请重新选择用户 ID。",
            {"code": "USER_REQUIRED"},
        )
    digest = token_hash(token)
    with connect_db(db_path) as db:
        row = db.execute(
            "SELECT user_id FROM user_sessions WHERE token_hash = ?", (digest,)
        ).fetchone()
    if not row:
        raise ApiError(
            HTTPStatus.UNAUTHORIZED,
            "身份凭证已失效，请重新选择用户 ID。",
            {"code": "USER_REQUIRED"},
        )
    return row["user_id"]


def next_revision(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()
    revision = int(row["value"]) + 1
    db.execute("UPDATE meta SET value = ? WHERE key = 'revision'", (revision,))
    return revision


def ensure_item_capacity(
    db: sqlite3.Connection, city_id: str, kind: str, amount: int = 1
) -> None:
    """Check both quotas within the same write transaction as the insertion."""
    item_count = db.execute(
        "SELECT COUNT(*) FROM items WHERE city_id = ? AND kind = ?", (city_id, kind)
    ).fetchone()[0]
    if item_count + amount > MAX_ITEMS_PER_KIND:
        raise ApiError(HTTPStatus.INSUFFICIENT_STORAGE, "当前城市这一分类的条目已达上限，请先整理现有内容。")
    total_count = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if total_count + amount > MAX_ITEMS_TOTAL:
        raise ApiError(HTTPStatus.INSUFFICIENT_STORAGE, "项目条目总量已达上限，请先整理现有内容。")


def sync_itinerary_attractions(db, row, city_name: str, user_id: str, now: str, *, allow_create: bool = True) -> int:
    def create_attraction(name):
        ensure_item_capacity(db, row['city_id'], 'attraction')
        payload = rated_payload('attraction', validate_payload('attraction', {'name': name}), city_name)
        item_id = uuid.uuid4().hex[:12]
        position = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM items WHERE city_id=? AND kind='attraction'", (row['city_id'],)).fetchone()[0]
        db.execute("INSERT INTO items(id,city_id,kind,position,payload,version,created_at,updated_at,created_by,updated_by) VALUES(?,?,'attraction',?,?,1,?,?,?,?)",
                   (item_id, row['city_id'], position, json.dumps(payload, ensure_ascii=False), now, now, user_id, user_id))
        revision = next_revision(db)
        db.execute("INSERT INTO activity(revision,action,item_id,city_id,kind,item_name,user_id,happened_at) VALUES(?,'create',?,?,'attraction',?,?,?)",
                   (revision, item_id, row['city_id'], name, user_id, now))
        # Name-only itinerary additions stay local until a normal edit enriches
        # them. They must not overwrite useful shared-city cache descriptions.
        return dict(payload, id=item_id)
    return itinerary_links.sync_itinerary(db, row, city_name, create_attraction, allow_create=allow_create)


def relink_city_itineraries(db, city_id: str, city_name: str, user_id: str, now: str) -> None:
    for row in db.execute("SELECT * FROM items WHERE city_id=? AND kind='itinerary'", (city_id,)).fetchall():
        sync_itinerary_attractions(db, row, city_name, user_id, now, allow_create=False)


def backfill_itinerary_links(db_path: Path) -> None:
    """One upgrade pass; reads/restarts never recreate explicitly deleted places."""
    with connect_db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM meta WHERE key='itinerary_links_v1'").fetchone():
            return
        now = utc_now()
        rows = db.execute("SELECT i.*,c.name AS city_name FROM items i JOIN cities c ON c.id=i.city_id WHERE i.kind='itinerary' ORDER BY i.position,i.id").fetchall()
        linked = False
        for row in rows:
            # An already-full city should still start. Existing sights link;
            # missing ones can be added after space is freed and the plan saved.
            db.execute('SAVEPOINT itinerary_backfill')
            try:
                sync_itinerary_attractions(db, row, row['city_name'], row['updated_by'], now)
            except ApiError as exc:
                db.execute('ROLLBACK TO itinerary_backfill')
                if exc.status != HTTPStatus.INSUFFICIENT_STORAGE:
                    raise
                sync_itinerary_attractions(db, row, row['city_name'], row['updated_by'], now, allow_create=False)
            finally:
                db.execute('RELEASE itinerary_backfill')
            linked = True
        if linked:
            next_revision(db)
        db.execute("INSERT INTO meta VALUES('itinerary_links_v1',1)")


def create_item(db_path: Path, kind: str, data: Any, user_id: str) -> dict[str, Any]:
    payload = validate_payload(kind, data)
    city_id = data.get("city_id", DEFAULT_CITY_ID)
    now = utc_now()
    item_id = uuid.uuid4().hex[:12]
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        city = db.execute("SELECT name FROM cities WHERE id = ?", (city_id,)).fetchone() if isinstance(city_id, str) else None
        if not city:
            raise ApiError(400, "请选择有效城市。")
        payload = rated_payload(kind, payload, city['name'])
        ensure_item_capacity(db, city_id, kind)
        position = db.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS position FROM items WHERE city_id = ? AND kind = ?",
            (city_id, kind),
        ).fetchone()["position"]
        db.execute(
            """
            INSERT INTO items(
                id, city_id, kind, position, payload, version, created_at,
                updated_at, created_by, updated_by
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                item_id,
                city_id,
                kind,
                position,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
                user_id,
                user_id,
            ),
        )
        revision = next_revision(db)
        db.execute(
            """
            INSERT INTO activity(revision, action, item_id, city_id, kind, item_name, user_id, happened_at)
            VALUES (?, 'create', ?, ?, ?, ?, ?, ?)
            """,
            (revision, item_id, city_id, kind, item_label(kind, payload), user_id, now),
        )
        queue_cached_place(db, city_id, kind, payload, now)
        row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        added = sync_itinerary_attractions(db, row, city['name'], user_id, now) if kind == 'itinerary' else 0
        if kind == 'attraction':
            relink_city_itineraries(db, city_id, city['name'], user_id, now)
        revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        result_item = item_with_links(db, row, city['name'])
    flush_place_cache(db_path)
    return {"revision": revision, "item": result_item, "auto_added_attractions": added}


def update_item(
    db_path: Path, kind: str, item_id: str, data: Any, user_id: str
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "内容格式不正确。")
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise ApiError(HTTPStatus.BAD_REQUEST, "版本号无效。")
    payload = validate_payload(kind, data)
    now = utc_now()
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT * FROM items WHERE id = ? AND kind = ?", (item_id, kind)
        ).fetchone()
        if not current:
            raise ApiError(HTTPStatus.NOT_FOUND, "这条内容已不存在。")
        city = db.execute("SELECT name FROM cities WHERE id=?", (current['city_id'],)).fetchone()
        if current["version"] != version:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "这条内容刚被其他人更新，请核对后再保存。",
                {"current": row_to_item(current, city['name'])},
            )
        if kind == 'itinerary':
            previous = json.loads(current['payload'])
            for key in daily_planner.VISIT_FIELDS:
                if key not in data and key in previous:
                    payload[key] = previous[key]
            try:
                daily_planner.validate_time_block_change(previous, payload)
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
            if any(payload.get(k) != previous.get(k) for k in ('location','attraction_names')) and 'poi' not in data:
                payload['poi'] = None
        payload = rated_payload(kind, payload, city['name'])
        new_version = version + 1
        db.execute(
            """
            UPDATE items
            SET payload = ?, version = ?, updated_at = ?, updated_by = ?
            WHERE id = ? AND kind = ? AND version = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                new_version,
                now,
                user_id,
                item_id,
                kind,
                version,
            ),
        )
        revision = next_revision(db)
        db.execute(
            """
            INSERT INTO activity(revision, action, item_id, city_id, kind, item_name, user_id, happened_at)
            VALUES (?, 'update', ?, ?, ?, ?, ?, ?)
            """,
            (revision, item_id, current["city_id"], kind, item_label(kind, payload), user_id, now),
        )
        queue_cached_place(db, current['city_id'], kind, payload, now)
        row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        added = sync_itinerary_attractions(db, row, city['name'], user_id, now) if kind == 'itinerary' else 0
        if kind == 'attraction':
            relink_city_itineraries(db, current['city_id'], city['name'], user_id, now)
        revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        result_item = item_with_links(db, row, city['name'])
    flush_place_cache(db_path)
    return {"revision": revision, "item": result_item, "auto_added_attractions": added}


def delete_item(
    db_path: Path, kind: str, item_id: str, data: Any, user_id: str
) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("version"), int):
        raise ApiError(HTTPStatus.BAD_REQUEST, "版本号无效。")
    version = data["version"]
    now = utc_now()
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT * FROM items WHERE id = ? AND kind = ?", (item_id, kind)
        ).fetchone()
        if not current:
            raise ApiError(HTTPStatus.NOT_FOUND, "这条内容已不存在。")
        if current["version"] != version:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "这条内容刚被其他人更新，请刷新后再删除。",
                {"current": row_to_item(current)},
            )
        current_item = row_to_item(current)
        db.execute('INSERT OR IGNORE INTO city_cache_imports VALUES(?,?)', (current['city_id'], now))
        db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        revision = next_revision(db)
        db.execute(
            """
            INSERT INTO activity(revision, action, item_id, city_id, kind, item_name, user_id, happened_at)
            VALUES (?, 'delete', ?, ?, ?, ?, ?, ?)
            """,
            (revision, item_id, current["city_id"], kind, item_label(kind, current_item), user_id, now),
        )
    return {"revision": revision, "deleted_id": item_id}


def change_city_itinerary(db_path: Path, data: Any, user_id: str, *, delete=False) -> dict[str, Any]:
    """Shift or remove all itinerary rows of one city in a single transaction."""
    if not isinstance(data, dict) or not isinstance(data.get('city_id'), str):
        raise ApiError(400, '请选择要编辑的城市。')
    city_id = data['city_id']
    if not isinstance(data.get('version'), str) or not data['version']:
        raise ApiError(400, '请重新打开城市编辑窗口。')
    if delete and data.get('confirm_delete') is not True:
        raise ApiError(400, '请确认移除该城市的全部行程。')
    start = None
    if not delete:
        raw_start = data.get('start_date')
        if not isinstance(raw_start, str) or not DATE_PATTERN.fullmatch(raw_start):
            raise ApiError(400, '请输入有效的开始日期。')
        try:
            start = datetime.strptime(raw_start, '%Y-%m-%d').date()
        except ValueError:
            raise ApiError(400, '请输入有效的开始日期。') from None
    now = utc_now()
    with connect_db(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        city = db.execute('SELECT name FROM cities WHERE id=?', (city_id,)).fetchone()
        if not city:
            raise ApiError(404, '该城市已不存在，请刷新后重试。')
        if not db.execute('SELECT 1 FROM users WHERE user_id=?', (user_id,)).fetchone():
            raise ApiError(401, '身份已失效，请重新选择用户 ID。')
        rows = db.execute("SELECT * FROM items WHERE city_id=? AND kind='itinerary' ORDER BY id", (city_id,)).fetchall()
        if not rows or data['version'] != project_itinerary.version(rows, city_id, city['name']):
            raise ApiError(409, '该城市的行程已被修改，本次未作任何更改。请载入最新版后重试。')
        payloads = [json.loads(row['payload']) for row in rows]
        offset = 0
        if not delete:
            first = min(datetime.strptime(payload['date'], '%Y-%m-%d').date() for payload in payloads)
            offset = (start - first).days
            try:
                for payload in payloads:
                    original = datetime.strptime(payload['date'], '%Y-%m-%d').date()
                    payload['date'] = (original + timedelta(days=offset)).isoformat()
            except (ValueError, OverflowError):
                raise ApiError(400, '调整后部分行程日期超出有效范围，请更换开始日期。') from None
        revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        if delete or offset:
            daily_plan_store.shift_city(db, city_id, offset, delete)
            for row, payload in zip(rows, payloads):
                if delete:
                    db.execute('DELETE FROM items WHERE id=?', (row['id'],))
                else:
                    db.execute('UPDATE items SET payload=?,version=version+1,updated_at=?,updated_by=? WHERE id=?',
                               (json.dumps(payload, ensure_ascii=False), now, user_id, row['id']))
                revision = next_revision(db)
                db.execute('''INSERT INTO activity(revision,action,item_id,city_id,kind,item_name,user_id,happened_at)
                              VALUES(?,?,?,?,'itinerary',?,?,?)''',
                           (revision, 'delete' if delete else 'update', row['id'], city_id,
                            item_label('itinerary', payload), user_id, now))
    return {'revision': revision, 'city_id': city_id, 'changed': len(rows) if delete or offset else 0,
            'deleted': delete, 'offset_days': offset}


def batch_delete_items(
    db_path: Path, kind: str, data: Any, user_id: str
) -> dict[str, Any]:
    """Delete one city's selected items atomically in this project only."""
    if kind not in ("attraction", "food"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "仅支持批量删除景点或美食。")
    if not isinstance(data, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "内容格式不正确。")
    city_id = data.get("city_id")
    if not isinstance(city_id, str) or not city_id or len(city_id) > 100:
        raise ApiError(HTTPStatus.BAD_REQUEST, "请选择有效城市。")
    selections = data.get("items")
    if not isinstance(selections, list) or not 1 <= len(selections) <= 250:
        raise ApiError(HTTPStatus.BAD_REQUEST, "请勾选 1–250 条景点或美食。")
    selected_ids = set()
    for selection in selections:
        if not isinstance(selection, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "所选内容格式不正确。")
        item_id, version = selection.get("id"), selection.get("version")
        if not isinstance(item_id, str) or not re.fullmatch(r"[a-f0-9]{12}", item_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "所选内容 ID 无效。")
        if type(version) is not int or version < 1:
            raise ApiError(HTTPStatus.BAD_REQUEST, "版本号无效。")
        if item_id in selected_ids:
            raise ApiError(HTTPStatus.BAD_REQUEST, "同一条内容不能重复勾选。")
        selected_ids.add(item_id)

    now = utc_now()
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM cities WHERE id = ?", (city_id,)).fetchone():
            raise ApiError(HTTPStatus.NOT_FOUND, "这座城市已不存在，请刷新后重试。")
        rows = []
        for selection in selections:
            current = db.execute(
                "SELECT * FROM items WHERE id = ? AND city_id = ? AND kind = ?",
                (selection["id"], city_id, kind),
            ).fetchone()
            if current is None:
                raise ApiError(
                    HTTPStatus.NOT_FOUND,
                    "部分内容已不存在或不属于当前城市和分类，本次未删除任何内容，请刷新后重试。",
                )
            if current["version"] != selection["version"]:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "部分内容刚被其他人更新，本次未删除任何内容，请刷新后重新勾选。",
                    {"current": row_to_item(current)},
                )
            rows.append(current)
        db.execute('INSERT OR IGNORE INTO city_cache_imports VALUES(?,?)', (city_id, now))
        for current in rows:
            db.execute("DELETE FROM items WHERE id = ?", (current["id"],))
            revision = next_revision(db)
            db.execute(
                """
                INSERT INTO activity(revision, action, item_id, city_id, kind, item_name, user_id, happened_at)
                VALUES (?, 'delete', ?, ?, ?, ?, ?, ?)
                """,
                (revision, current["id"], city_id, kind,
                 item_label(kind, row_to_item(current)), user_id, now),
            )
    return {
        "revision": revision,
        "deleted_ids": [selection["id"] for selection in selections],
        "deleted_count": len(rows),
        "city_id": city_id,
    }


def admin_state(db_path: Path) -> dict[str, Any]:
    state = snapshot(db_path)
    return {"project": state["project"], "cities": state["cities"], "users": list_users(db_path)}


def admin_change(db_path: Path, method: str, resource: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ApiError(400, "内容格式不正确。")
    now = utc_now()
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if resource == "project" and method == "PUT":
            name = validate_project_name(data.get("name"))
            code = data.get("project_code", "")
            if code or data.get("remove_password") is True:
                if data.get("remove_password") is True:
                    code = ""
                try:
                    validate_project_code(code, allow_empty=True)
                except ValueError as error:
                    raise ApiError(400, str(error)) from error
                salt, digest = create_secret_record(code)
                db.execute("UPDATE project_settings SET code_salt=?, code_hash=?, access_version=access_version+1 WHERE singleton=1", (salt, digest))
            db.execute("UPDATE project_settings SET project_name=?, updated_at=? WHERE singleton=1", (name, now))
        elif resource == "users":
            old = validate_user_id(data.get("user_id"))
            row = db.execute("SELECT * FROM users WHERE user_id=?", (old,)).fetchone()
            if method == "POST":
                if row:
                    raise ApiError(409, "用户 ID 已存在。")
                if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= MAX_USERS:
                    raise ApiError(400, "用户数量已达上限。")
                db.execute("INSERT INTO users VALUES (?, ?, ?)", (old, now, now))
            elif method in {"PUT", "DELETE"}:
                if not row:
                    raise ApiError(404, "用户不存在。")
                if method == "PUT":
                    new = validate_user_id(data.get("new_user_id"))
                    if new.casefold() == old.casefold() or db.execute("SELECT 1 FROM users WHERE user_id=?", (new,)).fetchone():
                        raise ApiError(409, "请使用不同且未被占用的 ID。")
                    db.execute("INSERT INTO users VALUES (?, ?, ?)", (new, row["created_at"], now))
                    for column in ("created_by", "updated_by"):
                        db.execute(f"UPDATE items SET {column}=? WHERE {column}=? COLLATE NOCASE", (new, old))
                    db.execute("UPDATE activity SET user_id=? WHERE user_id=? COLLATE NOCASE", (new, old))
                    # AI is optional: preserve drafts and idempotency receipts
                    # when its tables exist, without creating them on rename.
                    for table in ("ai_jobs", "ai_import_batches"):
                        if db.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                        ).fetchone():
                            db.execute(f"UPDATE {table} SET user_id=? WHERE user_id=? COLLATE NOCASE", (new, old))
                db.execute("DELETE FROM users WHERE user_id=?", (old,))
            else:
                raise ApiError(405, "操作不支持。")
        elif resource == "cities":
            if method == "POST":
                name = validate_city_name(data.get("name"))
                insert_city(db, name, now)
            elif method in {"PUT", "DELETE"}:
                city_id = data.get("id")
                if not isinstance(city_id, str) or not db.execute("SELECT 1 FROM cities WHERE id=?", (city_id,)).fetchone():
                    raise ApiError(404, "城市不存在。")
                if method == "PUT":
                    name = validate_city_name(data.get("name"))
                    if find_existing_city(db, name, excluding_id=city_id):
                        raise ApiError(409, "城市已存在，请使用不同的城市名称。")
                    db.execute("UPDATE cities SET name=?, updated_at=? WHERE id=?", (name, now, city_id))
                else:
                    if db.execute("SELECT COUNT(*) FROM cities").fetchone()[0] <= 1:
                        raise ApiError(409, "至少保留一个城市。")
                    if db.execute("SELECT 1 FROM items WHERE city_id=?", (city_id,)).fetchone():
                        raise ApiError(409, "城市中仍有内容，请先整理后再删除。")
                    db.execute("DELETE FROM city_cache_imports WHERE city_id=?", (city_id,))
                    db.execute("DELETE FROM cities WHERE id=?", (city_id,))
            else:
                raise ApiError(405, "操作不支持。")
        else:
            raise ApiError(404, "管理接口不存在。")
        next_revision(db)
    return admin_state(db_path)


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events.setdefault(key, collections.deque())
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            if len(self._events) > 5000:
                for stale_key in list(self._events)[:500]:
                    stale_events = self._events[stale_key]
                    while stale_events and stale_events[0] < cutoff:
                        stale_events.popleft()
                    if not stale_events:
                        self._events.pop(stale_key, None)
            return True


def ai_context(db_path: Path, city_id: str) -> dict[str, Any]:
    current = snapshot(db_path, city_id=city_id)
    city = next((entry for entry in current["cities"] if entry["id"] == city_id), None)
    if city is None:
        raise ApiError(404, "这个城市已不存在，请重新选择。")
    # Business fields inform the plan. IDs and versions stay in the local task
    # context and protect replacement without being sent to the AI provider.
    existing = {}
    for kind in ("attraction", "food", "itinerary"):
        keys = ("date", "start_time", "title", "location", "category", "notes") if kind == "itinerary" else (
            ("name", "category", "tags", "description", "duration", "in_itinerary") if kind == 'attraction' else ("name",))
        existing[kind] = [{key: item.get(key, "") for key in keys}
                          for item in current["items"][kind]][:250]
        for source, target in zip(current['items'][kind], existing[kind]):
            if kind == 'itinerary':
                target['attraction_names'] = [item['name'] for item in source['linked_attractions']]
            elif kind == 'attraction':
                target['itinerary_dates'] = sorted({item['date'] for item in source['itinerary_refs']})
    return {"city": city, "existing": existing,
            "itinerary_snapshot": [{"id": item["id"], "version": item["version"], "date": item["date"]}
                                   for item in current["items"]["itinerary"]]}


def ai_replacement_rows(db: sqlite3.Connection, city_id: str, job: sqlite3.Row | None,
                        cleaned: list[tuple[str, dict[str, str]]]) -> tuple[str, list[sqlite3.Row]]:
    """Validate a task's saved replacement scope in the import write transaction."""
    if job is None or 'request_json' not in job.keys():
        return 'append', []
    request = json.loads(job['request_json'])
    mode = request.get('planning_mode', 'append')
    if mode == 'append':
        return mode, []
    if mode not in ('replace_all', 'replace_day'):
        raise ApiError(409, '这份任务的规划模式无效，请重新生成。')
    context = json.loads(job['context_json'])
    replacement = context.get('replacement')
    target_date = request.get('target_date', '')
    if (not isinstance(replacement, dict) or replacement.get('planning_mode') != mode
            or replacement.get('target_date', '') != target_date
            or not isinstance(replacement.get('items'), list)):
        raise ApiError(409, '这份任务缺少原行程快照，请重新生成后再替换。')
    if request.get('kinds') != ['itinerary'] or any(kind != 'itinerary' for kind, _ in cleaned):
        raise ApiError(400, '重新规划只能替换行程，不能修改景点、美食或交通。')
    try:
        start = datetime.strptime(request['start_date'], '%Y-%m-%d').date()
        days = request['days']
        if type(days) is not int or days < 1:
            raise ValueError('invalid days')
        expected_dates = {(start + timedelta(days=offset)).isoformat() for offset in range(days)}
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ApiError(400, '重新规划的日期范围无效，请重新生成。') from error
    if mode == 'replace_day' and (days != 1 or target_date != start.isoformat()):
        raise ApiError(400, '单日重新规划只能替换指定日期。')
    if {payload['date'] for _, payload in cleaned} != expected_dates:
        raise ApiError(400, '请为重新规划范围内的每一天至少保留一项行程，且不要加入其他日期。')
    expected = {}
    for item in replacement['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not re.fullmatch(r'[0-9a-f]{12}', item['id'])
                or type(item.get('version')) is not int or item['version'] < 1
                or item['id'] in expected):
            raise ApiError(409, '原行程快照无效，请重新生成后再替换。')
        expected[item['id']] = item['version']
    rows = db.execute("SELECT * FROM items WHERE city_id=? AND kind='itinerary' ORDER BY position,id",
                      (city_id,)).fetchall()
    if mode == 'replace_day':
        rows = [row for row in rows if json.loads(row['payload']).get('date') == target_date]
    if {row['id']: row['version'] for row in rows} != expected:
        raise ApiError(409, '生成后，要替换的行程已被修改、新增或删除。原行程保持不变，请刷新并重新生成。',
                       {'code': 'ITINERARY_CHANGED'})
    return mode, rows


def import_ai_items(db_path: Path, city_id: str, user_id: str,
                    items: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
    if not isinstance(items, list) or not items:
        raise ApiError(400, "请至少选择一条内容导入。")
    cleaned = []
    for item in items:
        if not isinstance(item, dict) or item.get("kind") not in {"attraction", "food", "itinerary"}:
            raise ApiError(400, "AI 导入的内容类别无效。")
        cleaned.append((item["kind"], validate_payload(item["kind"], item.get("data"))))

    def signature(kind: str, payload: dict[str, str]) -> tuple[str, ...]:
        keys = ("date", "start_time", "title") if kind == "itinerary" else ("name",)
        return (kind,) + tuple("".join(unicodedata.normalize("NFKC", payload.get(key, "")).casefold().split()) for key in keys)

    with connect_db(db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS ai_import_batches (job_id TEXT PRIMARY KEY, city_id TEXT NOT NULL, user_id TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT * FROM ai_import_batches WHERE job_id = ?", (job_id,)).fetchone()
        if prior:
            if prior["city_id"] != city_id or prior["user_id"] != user_id:
                raise ApiError(403, "无法访问这个导入批次。")
            return json.loads(prior["result"])
        if not db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
            raise ApiError(401, "用户身份已失效，请重新选择 ID。")
        current_city = db.execute("SELECT name FROM cities WHERE id = ?", (city_id,)).fetchone()
        if not current_city:
            raise ApiError(404, "这个城市已被删除，无法导入。")
        job = None
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_jobs'").fetchone():
            job = db.execute("SELECT * FROM ai_jobs WHERE id = ?", (job_id,)).fetchone()
            if job:
                from ai_service import city_identity
                if city_identity(job["city_name"]) != city_identity(current_city["name"]):
                    raise ApiError(409, "本次 AI 任务的城市已被修改，请为当前城市重新生成。")
                if 'city_id' in job.keys() and (job['city_id'] != city_id or job['user_id'] != user_id):
                    raise ApiError(403, '这份 AI 任务不属于当前城市或用户。')
        mode, replaced_rows = ai_replacement_rows(db, city_id, job, cleaned)
        now = utc_now()
        # Deletions, inserts, activity and receipt all commit together. Any error
        # rolls the entire change back; a retry reads the receipt before deleting.
        for row in replaced_rows:
            db.execute('DELETE FROM items WHERE id=?', (row['id'],))
            revision = next_revision(db)
            db.execute("INSERT INTO activity(revision,action,item_id,city_id,kind,item_name,user_id,happened_at) VALUES(?,'delete',?,?,'itinerary',?,?,?)",
                       (revision, row['id'], city_id, item_label('itinerary', json.loads(row['payload'])), user_id, now))
        seen = {signature(row["kind"], json.loads(row["payload"])): row['id'] for row in
                db.execute("SELECT id,kind,payload FROM items WHERE city_id = ?", (city_id,))}
        created, skipped, item_ids = 0, 0, []
        selected_item_ids = []
        revision = int(db.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()["value"])
        for kind, payload in cleaned:
            payload = rated_payload(kind, payload, current_city['name'])
            key = signature(kind, payload)
            if key in seen:
                skipped += 1
                selected_item_ids.append(seen[key])
                continue
            ensure_item_capacity(db, city_id, kind)
            position = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM items WHERE city_id=? AND kind=?", (city_id, kind)).fetchone()[0]
            item_id = uuid.uuid4().hex[:12]
            db.execute("INSERT INTO items(id,city_id,kind,position,payload,version,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,1,?,?,?,?)",
                       (item_id, city_id, kind, position, json.dumps(payload, ensure_ascii=False), now, now, user_id, user_id))
            revision = next_revision(db)
            db.execute("INSERT INTO activity(revision,action,item_id,city_id,kind,item_name,user_id,happened_at) VALUES(?,'create',?,?,?,?,?,?)",
                       (revision, item_id, city_id, kind, item_label(kind, payload), user_id, now))
            queue_cached_place(db, city_id, kind, payload, now)
            seen[key] = item_id
            selected_item_ids.append(item_id)
            item_ids.append(item_id)
            created += 1
        # All selected full attraction records are present before itinerary
        # linking; no empty auto-created record can displace a selected one.
        relink_city_itineraries(db, city_id, current_city['name'], user_id, now)
        auto_added = 0
        for item_id in item_ids:
            row = db.execute("SELECT * FROM items WHERE id=? AND kind='itinerary'", (item_id,)).fetchone()
            if row:
                auto_added += sync_itinerary_attractions(db, row, current_city['name'], user_id, now)
        revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        result = {"created": created, "skipped": skipped, "item_ids": item_ids, "city_id": city_id, "revision": revision,
                  "auto_added_attractions": auto_added}
        if mode != 'append':
            result.update(deleted=len(replaced_rows), planning_mode=mode)
        db.execute("INSERT INTO ai_import_batches VALUES(?,?,?,?,?)", (job_id, city_id, user_id, json.dumps(result), now))
    flush_place_cache(db_path)
    return result


class TripHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32
    max_worker_threads = 16

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: Any,
        db_path: Path,
        *,
        cookie_secure: bool,
        project_code: str,
        public_origin: str,
        admin_password: str = "",
    ):
        self._worker_slots = threading.BoundedSemaphore(self.max_worker_threads)
        self.ai_service = None
        self.metro_store = None
        self.amap_service = AMapService()
        self.project_store = None
        self._ai_services = {}
        self._ai_lock = threading.Lock()
        self._ai_api_key = ''
        self._ai_model = ''
        super().__init__(server_address, handler)
        self.db_path = db_path
        self.cookie_secure = cookie_secure
        self.project_code = project_code
        self.admin_password = admin_password
        self.public_origin = public_origin.rstrip("/")
        self.rate_limiter = SlidingWindowLimiter()

    def server_close(self) -> None:
        if self.metro_store:
            self.metro_store.close()
        for service in set(self._ai_services.values()) | ({self.ai_service} if self.ai_service else set()):
            service.close()
        super().server_close()

    def project_ai(self, project_id: str) -> Any:
        with self.project_store.lock:
            try:
                path = self.project_store.resolve(project_id)
            except KeyError as error:
                raise ApiError(404, '项目已删除或不存在。') from error
            if project_id == 'main' and self.ai_service:
                return self.ai_service
            if not self._ai_api_key:
                return None
            with self._ai_lock:
                if project_id not in self._ai_services:
                    from ai_service import AIService
                    self._ai_services[project_id] = AIService(path, api_key=self._ai_api_key, model=self._ai_model,
                        get_context=partial(ai_context,path), normalize_item=validate_payload, import_items=partial(import_ai_items,path))
                if project_id == 'main':
                    self.ai_service = self._ai_services[project_id]
                return self._ai_services[project_id]

    API_FIELDS = {"DEEPSEEK_API_KEY", "AMAP_JS_KEY", "AMAP_SECURITY_JS_CODE", "AMAP_WEB_SERVICE_KEY"}

    def api_settings_status(self) -> dict:
        values = {"DEEPSEEK_API_KEY": self._ai_api_key, "AMAP_JS_KEY": self.amap_service.js_key,
                  "AMAP_SECURITY_JS_CODE": self.amap_service.security_code,
                  "AMAP_WEB_SERVICE_KEY": self.amap_service.web_key}
        return {"configured": {key: bool(value) for key, value in values.items()}}

    def apply_api_settings(self, values: dict) -> None:
        if "DEEPSEEK_API_KEY" in values:
            self._ai_api_key = values["DEEPSEEK_API_KEY"]
            for service in set(self._ai_services.values()) | ({self.ai_service} if self.ai_service else set()):
                service._api_key = self._ai_api_key
                service.enabled = bool(self._ai_api_key)
        for key, attr in (("AMAP_JS_KEY", "js_key"), ("AMAP_SECURITY_JS_CODE", "security_code"),
                          ("AMAP_WEB_SERVICE_KEY", "web_key")):
            if key in values:
                setattr(self.amap_service, attr, values[key])

    def save_api_settings(self, data: Any) -> dict:
        if not isinstance(data, dict) or set(data) - self.API_FIELDS:
            raise ApiError(400, "API 配置字段不正确。")
        if any(not isinstance(value, str) or len(value) > 500 or any(c.isspace() for c in value)
               for value in data.values()):
            raise ApiError(400, "密钥最多 500 个字符，不能包含空白字符。")
        with self.project_store.lock:
            path = self.db_path.parent / "api-settings.json"
            values = json.loads(path.read_text()) if path.exists() else {}
            values.update(data)
            temporary = path.with_name(".api-settings-" + secrets.token_hex(8) + ".tmp")
            try:
                with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
                    json.dump(values, file)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self.apply_api_settings(values)
            return self.api_settings_status()

    def delete_project(self, project_id: str, confirm_name: str) -> dict:
        # AI submission/import and deletion share this lock: a job cannot be
        # queued after the deletion guard checked the project's durable jobs.
        with self.project_store.lock:
            result = self.project_store.delete(project_id, confirm_name)
            with self._ai_lock:
                if project_id == 'main':
                    service, self.ai_service = self.ai_service, None
                else:
                    service = self._ai_services.pop(project_id, None)
                if service:
                    service.close()
            return result

    def process_request(self, request: Any, client_address: Any) -> None:
        self._worker_slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class TripRequestHandler(SimpleHTTPRequestHandler):
    server_version = "ShanghaiTrip/2.0"
    sys_version = ""

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        policy = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        if self.server.amap_service.config('main')['map_enabled'] and urlparse(self.path).path in ('/', '/index.html'):
            policy = (
                "default-src 'self'; img-src 'self' data: blob: https://*.amap.com https://*.autonavi.com; "
                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-eval' https://webapi.amap.com; "
                "connect-src 'self' https://*.amap.com https://*.autonavi.com; worker-src 'self' blob:; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
        if urlparse(self.path).path.lower().endswith('.svg'):
            policy = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        self.send_header("Content-Security-Policy", policy)
        if not urlparse(self.path).path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    @property
    def db_path(self) -> Path:
        project_id = self.project_id
        if self.server.project_store is None and project_id == 'main':
            return self.server.db_path
        try:
            return self.server.project_store.resolve(project_id)
        except KeyError as error:
            raise ApiError(404, '项目不存在，请检查项目链接。') from error

    @property
    def project_id(self) -> str:
        map_scope = re.match(r'^/_AMapService/(main|[0-9a-f]{16})/', urlparse(self.path).path)
        project_id = map_scope.group(1) if map_scope else self.headers.get('X-Trip-Project') or parse_qs(urlparse(self.path).query).get('project', ['main'])[0]
        if not re.fullmatch(r'main|[0-9a-f]{16}', project_id):
            raise ApiError(404, '项目不存在，请检查项目链接。')
        return project_id

    def scoped_cookie_name(self, base_name: str) -> str:
        if base_name in {'trip_session', 'trip_project'} and self.project_id != 'main':
            return f'{base_name}_{self.project_id}'
        return base_name

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def send_json(
        self,
        status: int,
        data: Any | None = None,
        *,
        etag: str | None = None,
        extra_headers: dict[str, str] | list[tuple[str, str]] | None = None,
    ) -> None:
        body = b"" if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if data is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", etag)
        headers = extra_headers.items() if isinstance(extra_headers, dict) else (extra_headers or [])
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def read_json(self) -> Any:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "请求缺少内容长度。")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "内容长度无效。") from exc
        maximum = MAX_AI_BODY_BYTES if urlparse(self.path).path.startswith('/api/ai/jobs') else MAX_BODY_BYTES
        if length < 0 or length > maximum:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"一次提交的内容不能超过 {maximum // 1024} KB。")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except TimeoutError as exc:
            raise ApiError(HTTPStatus.REQUEST_TIMEOUT, "请求读取超时，请重试。") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 内容格式不正确。") from exc

    def handle_api_error(self, error: Exception) -> None:
        if isinstance(error, (MetroMapError, AMapError)):
            self.send_json(error.status, {"error": error.message})
            return
        if isinstance(error, ApiError):
            self.send_json(error.status, {"error": error.message, **error.extra})
            return
        from ai_service import AIError
        if isinstance(error, AIError):
            self.send_json(error.status, {"error": error.message, **error.extra})
            return
        print(f"Unhandled API error: {error}", file=sys.stderr)
        self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "服务器暂时无法完成请求。"})

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            if path.startswith('/_AMapService/'):
                self.require_project_access()
                authenticate(self.db_path, self.session_token())
                match = re.fullmatch(r'/_AMapService/(main|[0-9a-f]{16})(/.*)', path)
                if not match:
                    raise ApiError(404, '地图接口不存在。')
                body, content_type = self.server.amap_service.proxy(match.group(2), urlparse(self.path).query)
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return
            if not path.startswith("/api/"):
                super().do_GET()
                return
            if path.startswith('/api/metro-maps/image/'):
                self.serve_metro_image(path)
                return
            if path == "/api/health":
                with connect_db(self.server.db_path) as db:
                    revision = db.execute(
                        "SELECT value FROM meta WHERE key = 'revision'"
                    ).fetchone()["value"]
                self.send_json(
                    HTTPStatus.OK,
                    {"status": "ok", "revision": revision, "time": utc_now()},
                )
                return
            if path == "/api/projects":
                self.send_json(200, {"projects": self.server.project_store.list_projects()})
                return
            if path == "/api/project-session":
                self.send_json(
                    HTTPStatus.OK,
                    {"unlocked": self.has_project_access()},
                )
                return
            if path == "/api/config":
                self.send_json(HTTPStatus.OK, {"project_code_required": not verify_project_code(self.db_path, "")})
                return
            if path == "/api/admin/session":
                self.send_json(200, {"authenticated": self.has_admin_access(), "enabled": bool(self.server.admin_password)})
                return
            if path.startswith("/api/admin/"):
                self.require_admin_access()
                if path == "/api/admin/api-settings":
                    self.send_json(200, self.server.api_settings_status())
                    return
                if path == '/api/admin/projects':
                    self.send_json(200, {'projects': self.server.project_store.list_projects(), 'current_project_id': self.project_id})
                    return
                if path != "/api/admin/state":
                    raise ApiError(404, "接口不存在。")
                self.send_json(200, admin_state(self.db_path))
                return
            self.require_project_access()
            if path == '/api/day-plan/route.png':
                authenticate(self.db_path, self.session_token())
                query = parse_qs(urlparse(self.path).query)
                plan = daily_plan_store.get(sys.modules[__name__], self.db_path, query.get('city_id',[''])[0], query.get('date',[''])[0])
                import route_image
                preview = route_image.preview(plan)
                if not preview:
                    raise ApiError(404, '当天还没有已保存的路线图。')
                etag = '"route-' + preview['version'] + '"'
                unchanged = self.headers.get('If-None-Match') == etag
                mapped = None if unchanged else route_image.route_map(plan)
                if not unchanged and not mapped:
                    raise ApiError(404, '当天没有可显示的路线轨迹。')
                self.send_response(304 if unchanged else 200)
                self.send_header('Content-Type','image/png')
                self.send_header('Cache-Control','private, no-cache')
                self.send_header('Vary','Cookie, X-Trip-Project')
                self.send_header('ETag',etag)
                if mapped:
                    self.send_header('Content-Length',str(len(mapped[0])))
                self.end_headers()
                if mapped:
                    self.wfile.write(mapped[0])
                return
            if path == '/api/day-plan':
                authenticate(self.db_path, self.session_token())
                query = parse_qs(urlparse(self.path).query)
                self.send_json(200, daily_plan_store.get(sys.modules[__name__], self.db_path, query.get('city_id',[''])[0], query.get('date',[''])[0]))
                return
            if path == '/api/maps/config':
                authenticate(self.db_path, self.session_token())
                self.send_json(200, self.server.amap_service.config(self.project_id))
                return
            if path == '/api/travel-guidance':
                authenticate(self.db_path, self.session_token())
                city_id = parse_qs(urlparse(self.path).query).get('city_id', [''])[0]
                self.send_json(200, {'guidance': guidance_state(self.db_path, city_id)})
                return
            if path == '/api/itinerary/export':
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                file_format = query.get('format', [''])[0]
                scope = query.get('scope', ['city'])[0]
                city_id = query.get('city_id', [''])[0]
                if file_format not in {*EXPORT_MIME_TYPES, 'image'} or scope not in {'city', 'all'}:
                    raise ApiError(400, '请选择行程图片、Word 或 Excel，以及有效的导出范围。')
                if scope == 'city' and not city_id:
                    raise ApiError(400, '请选择要导出的城市。')
                data = itinerary_export_snapshot(self.db_path, city_id if scope == 'city' else None)
                if file_format == 'image':
                    self.send_json(200, build_image_data(data, self.server.amap_service))
                    return
                body = (build_docx if file_format == 'docx' else build_xlsx)(data)
                name = export_filename(data, file_format)
                self.send_response(200)
                self.send_header('Content-Type', EXPORT_MIME_TYPES[file_format])
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Disposition', f'attachment; filename="itinerary.{file_format}"; filename*=UTF-8\'\'{quote(name, safe="")}')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return
            if path == '/api/metro-map':
                city_id = parse_qs(urlparse(self.path).query).get('city_id', [''])[0]
                city = self.metro_city(city_id)
                self.send_json(200, self.server.metro_store.describe(city['id'], city['name']))
                return
            if path == "/api/ai/config":
                self.db_path  # Validate the requested project before creating a worker.
                service = self.server.project_ai(self.project_id)
                self.send_json(200, {"enabled": bool(service and service.enabled),
                                     "model": service.model if service else os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash-vision-exp")})
                return
            ai_match = re.fullmatch(r"/api/ai/jobs/([a-zA-Z0-9_-]+)", path)
            if ai_match:
                service, user_id, device_key = self.ai_request_context()
                self.send_json(200, service.get_job(user_id, device_key, ai_match.group(1)))
                return
            if path == "/api/users":
                self.send_json(HTTPStatus.OK, {"users": list_users(self.db_path)})
                return
            if path == "/api/session":
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(HTTPStatus.OK, {"user_id": user_id})
                return
            if path == "/api/snapshot":
                city_id = parse_qs(urlparse(self.path).query).get("city_id", [None])[0]
                state = snapshot(self.db_path, city_id=city_id)
                city_key = hashlib.sha256((city_id or "all").encode()).hexdigest()[:16]
                etag = f'"revision-{state["revision"]}-{city_key}"' if city_id else f'"revision-{state["revision"]}"'
                etag = etag[:-1] + '-scenic-' + state['scenic_catalog_version'] + '"'
                etag = etag[:-1] + '-cities-' + state['city_metadata_version'] + '"'
                if self.project_id != 'main':
                    etag = etag[:-1] + '-' + self.project_id + '"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_json(HTTPStatus.NOT_MODIFIED, etag=etag)
                    return
                self.send_json(HTTPStatus.OK, state, etag=etag)
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "接口不存在。")
        except Exception as error:  # pragma: no cover - final safety net
            self.handle_api_error(error)

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            self.validate_write_request()
            if path.startswith("/api/admin/"):
                self.handle_admin_write("POST", path)
                return
            if path == "/api/projects":
                self.enforce_rate_limit("access")
                data = self.read_json()
                if not isinstance(data, dict):
                    raise ApiError(400, "项目资料格式不正确。")
                name = validate_project_name(data.get("name"))
                try:
                    code = validate_project_code(data.get("project_code", ""), allow_empty=True)
                    with self.server.project_store.lock:
                        require_unique_project_code(self.server.project_store, code)
                        for existing in self.server.project_store.list_projects():
                            flush_place_cache(self.server.project_store.resolve(existing['id']))
                        project = self.server.project_store.create(name, code)
                        record = project_auth_record(self.server.project_store.resolve(project['id']))
                except ValueError as error:
                    raise ApiError(400, str(error)) from error
                token = create_project_access_token(record['session_secret'], record['access_version'])
                self.send_json(201, {'project': project, 'project_id': project['id'], 'unlocked': True},
                               extra_headers={'Set-Cookie': self.build_cookie('trip_project', token,
                                              PROJECT_ACCESS_MAX_AGE, project_id=project['id'])})
                return
            if path == "/api/project-entry":
                data = self.read_json()
                try:
                    code = validate_project_code(data.get('project_code') if isinstance(data, dict) else None)
                except ValueError as error:
                    raise ApiError(400, str(error)) from error
                with self.server.project_store.lock:
                    matches = projects_matching_code(self.server.project_store, code)
                    if not matches:
                        raise ApiError(403, "项目口令不正确，请确认后重试。")
                    if len(matches) != 1:
                        raise ApiError(409, "该口令对应多个项目，请联系管理员为项目设置不同口令。")
                    project = matches[0]
                    record = project_auth_record(self.server.project_store.resolve(project['id']))
                    access_token = create_project_access_token(record['session_secret'], record['access_version'])
                    self.send_json(200, {'unlocked': True, 'project_id': project['id']},
                                   extra_headers={'Set-Cookie': self.build_cookie(
                                       'trip_project', access_token, PROJECT_ACCESS_MAX_AGE,
                                       project_id=project['id'])})
                return
            if path == "/api/project-session":
                data = self.read_json()
                submitted_code = data.get("project_code", "") if isinstance(data, dict) else ""
                record = project_auth_record(self.db_path)
                if not verify_secret(submitted_code, record["code_salt"], record["code_hash"]):
                    raise ApiError(HTTPStatus.FORBIDDEN, "项目口令不正确。")
                access_token = create_project_access_token(
                    record["session_secret"], record["access_version"]
                )
                self.send_json(
                    HTTPStatus.OK,
                    {"unlocked": True},
                    extra_headers={
                        "Set-Cookie": self.build_cookie(
                            "trip_project", access_token, PROJECT_ACCESS_MAX_AGE
                        )
                    },
                )
                return

            self.require_project_access()
            if path == '/api/day-plan':
                self.enforce_rate_limit('write')
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(201, daily_plan_store.change_day(sys.modules[__name__], self.db_path, self.read_json(), user_id))
                return
            if path == '/api/day-plan/route':
                self.enforce_rate_limit('write')
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(200, daily_plan_store.save_route(sys.modules[__name__], self.db_path, self.read_json(), user_id, self.server.amap_service))
                return
            if path in ('/api/day-plan/evaluate', '/api/day-plan/apply'):
                self.enforce_rate_limit('write')
                user_id = authenticate(self.db_path, self.session_token())
                data = self.read_json()
                if path.endswith('/evaluate'):
                    result = daily_plan_store.evaluate(sys.modules[__name__], self.db_path, data, user_id, self.server.amap_service)
                else:
                    result = daily_plan_store.apply(sys.modules[__name__], self.db_path, data, user_id)
                self.send_json(200, result)
                return
            if path in ('/api/maps/search', '/api/maps/route'):
                authenticate(self.db_path, self.session_token())
                data = self.read_json()
                if not isinstance(data, dict):
                    raise ApiError(400, '地图请求格式无效。')
                city = self.metro_city(data.get('city_id'))
                action = self.server.amap_service.search if path.endswith('/search') else self.server.amap_service.route
                self.send_json(200, action(data, city['name']))
                return
            if path == '/api/day-plan/ai-apply':
                self.enforce_rate_limit('write')
                import daily_ai
                data = self.read_json()
                with self.server.project_store.lock:
                    service, user_id, device_key = self.ai_request_context()
                    try:
                        result = daily_ai.apply_job(sys.modules[__name__], service, self.db_path, data, user_id, device_key)
                    except (ValueError, TypeError, KeyError) as exc:
                        raise ApiError(400, str(exc)) from None
                self.send_json(200, result)
                return
            if path == "/api/ai/jobs":
                data = self.read_json()
                with self.server.project_store.lock:
                    service, user_id, device_key = self.ai_request_context()
                    if isinstance(data, dict) and data.get('purpose') in {'daily_select','daily_choose','daily_adjust','daily_duration','daily_hours'}:
                        import daily_ai
                        data['_daily_context'] = daily_ai.prepare(sys.modules[__name__], self.db_path, data, user_id)
                    if isinstance(data, dict) and data.get('purpose') == 'travel_guidance':
                        data['_guidance_context'] = travel_guidance.prepare(sys.modules[__name__], self.db_path, data)
                    result = service.create_job(user_id, device_key, data)
                self.send_json(202, result)
                return
            ai_match = re.fullmatch(r"/api/ai/jobs/([a-zA-Z0-9_-]+)/import", path)
            if ai_match:
                data = self.read_json()
                with self.server.project_store.lock:
                    service, user_id, device_key = self.ai_request_context()
                    result = service.import_job(user_id, device_key, ai_match.group(1), data)
                self.send_json(200, result)
                return
            self.enforce_rate_limit("session" if path == "/api/session" else "write")
            data = self.read_json()
            if path == '/api/metro-map/update':
                authenticate(self.db_path, self.session_token())
                if not isinstance(data, dict) or set(data) != {'city_id'}:
                    raise ApiError(400, '请仅提供要更新线路图的城市。')
                city = self.metro_city(data['city_id'])
                self.send_json(202, self.server.metro_store.request_update(city['id'], city['name']))
                return
            if path == "/api/session":
                identity = claim_identity(self.db_path, data, self.session_token())
                self.send_json(
                    HTTPStatus.OK,
                    identity,
                    extra_headers={
                        "Set-Cookie": self.build_cookie(
                            "trip_session", identity.pop("token"), 31536000
                        )
                    },
                )
                return
            if path == "/api/cities":
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(201, create_city(self.db_path, data, user_id))
                return
            bulk_match = re.fullmatch(r"/api/items/(attraction|food)/batch-delete", path)
            if bulk_match:
                user_id = authenticate(self.db_path, self.session_token())
                result = batch_delete_items(self.db_path, bulk_match.group(1), data, user_id)
                self.send_json(HTTPStatus.OK, result)
                return
            match = re.fullmatch(r"/api/items/(attraction|transit|food|itinerary)", path)
            if not match:
                raise ApiError(HTTPStatus.NOT_FOUND, "接口不存在。")
            user_id = authenticate(self.db_path, self.session_token())
            result = create_item(self.db_path, match.group(1), data, user_id)
            self.send_json(HTTPStatus.CREATED, result)
        except Exception as error:
            self.handle_api_error(error)

    def metro_city(self, city_id: Any) -> dict:
        if not isinstance(city_id, str) or not city_id or len(city_id) > 100:
            raise ApiError(400, '请选择当前项目中的城市。')
        with connect_db(self.db_path) as db:
            city = db.execute('SELECT id, name FROM cities WHERE id = ?', (city_id,)).fetchone()
        if not city:
            raise ApiError(404, '当前项目中没有该城市。')
        return dict(city)

    def serve_metro_image(self, path: str) -> None:
        match = re.fullmatch(r'/api/metro-maps/image/([a-z0-9][a-z0-9-]{0,63})/([a-f0-9]{64})\.(svg|png|jpg)', path)
        asset = self.server.metro_store.asset_path(*match.groups()) if match else None
        if asset is None:
            raise ApiError(404, '线路图文件不存在。')
        etag = '"' + match.group(2) + '"'
        unchanged = self.headers.get('If-None-Match') == etag
        self.send_response(304 if unchanged else 200)
        self.send_header('ETag', etag)
        self.send_header('Cache-Control', 'public, max-age=86400, immutable')
        if not unchanged:
            self.send_header('Content-Type', {'svg': 'image/svg+xml', 'png': 'image/png', 'jpg': 'image/jpeg'}[match.group(3)])
            self.send_header('Content-Length', str(asset.stat().st_size))
        self.end_headers()
        if not unchanged:
            with asset.open('rb') as stream:
                self.copyfile(stream, self.wfile)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            self.validate_write_request()
            if urlparse(self.path).path.startswith("/api/admin/"):
                self.handle_admin_write("PUT", urlparse(self.path).path)
                return
            self.require_project_access()
            self.enforce_rate_limit("write")
            path = urlparse(self.path).path
            if path == '/api/day-plan':
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(200, daily_plan_store.save(sys.modules[__name__], self.db_path, self.read_json(), user_id))
                return
            if path == '/api/project-itinerary':
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(200, change_city_itinerary(self.db_path, self.read_json(), user_id))
                return
            if path == '/api/travel-guidance':
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(200, change_guidance(self.db_path, self.read_json(), user_id))
                return
            match = re.fullmatch(
                r"/api/items/(attraction|transit|food|itinerary)/([a-f0-9]{12})", path
            )
            if not match:
                raise ApiError(HTTPStatus.NOT_FOUND, "接口不存在。")
            user_id = authenticate(self.db_path, self.session_token())
            result = update_item(
                self.db_path,
                match.group(1),
                unquote(match.group(2)),
                self.read_json(),
                user_id,
            )
            self.send_json(HTTPStatus.OK, result)
        except Exception as error:
            self.handle_api_error(error)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            self.validate_write_request()
            path = urlparse(self.path).path
            if path.startswith("/api/admin/"):
                self.handle_admin_write("DELETE", path)
                return
            if path == "/api/project-session":
                self.read_json()
                self.send_json(
                    HTTPStatus.OK,
                    {"unlocked": False},
                    extra_headers=[
                        ("Set-Cookie", self.clear_cookie("trip_project")),
                        ("Set-Cookie", self.clear_cookie("trip_session")),
                    ],
                )
                return
            self.require_project_access()
            self.enforce_rate_limit("write")
            if path == '/api/day-plan':
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(200, daily_plan_store.change_day(sys.modules[__name__], self.db_path, self.read_json(), user_id, delete=True))
                return
            if path == '/api/project-itinerary':
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(200, change_city_itinerary(self.db_path, self.read_json(), user_id, delete=True))
                return
            if path == '/api/travel-guidance':
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(200, change_guidance(self.db_path, self.read_json(), user_id, delete=True))
                return
            match = re.fullmatch(
                r"/api/items/(attraction|transit|food|itinerary)/([a-f0-9]{12})", path
            )
            if not match:
                raise ApiError(HTTPStatus.NOT_FOUND, "接口不存在。")
            user_id = authenticate(self.db_path, self.session_token())
            result = delete_item(
                self.db_path,
                match.group(1),
                unquote(match.group(2)),
                self.read_json(),
                user_id,
            )
            self.send_json(HTTPStatus.OK, result)
        except Exception as error:
            self.handle_api_error(error)

    def log_message(self, fmt: str, *args: Any) -> None:
        message = fmt % args
        if '/_AMapService/' in message:
            message = re.sub(r'\?[^ ]*', '?[map-query-redacted]', message)
        print(f"{self.address_string()} [{self.log_date_time_string()}] {message}", file=sys.stderr)

    def cookie_value(self, base_name: str) -> str:
        base_name = self.scoped_cookie_name(base_name)
        raw_cookie = self.headers.get("Cookie", "")
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except http.cookies.CookieError:
            return ""
        secure = self.server.cookie_secure  # type: ignore[attr-defined]
        cookie_name = f"__Host-{base_name}" if secure else base_name
        morsel = cookie.get(cookie_name)
        return morsel.value if morsel else ""

    def session_token(self) -> str:
        return self.cookie_value("trip_session")

    def ai_request_context(self) -> tuple[Any, str, str]:
        user_id = authenticate(self.db_path, self.session_token())
        service = self.server.project_ai(self.project_id)
        if service is None or not service.enabled:
            raise ApiError(503, "AI 暂未配置，请联系项目管理员。")
        return service, user_id, token_hash(self.project_access_token())

    def project_access_token(self) -> str:
        return self.cookie_value("trip_project")

    def has_project_access(self) -> bool:
        record = project_auth_record(self.db_path)
        return validate_project_access_token(
            self.project_access_token(),
            record["session_secret"], record["access_version"],
        )

    def has_admin_access(self) -> bool:
        return bool(self.server.admin_password) and validate_admin_access_token(
            self.cookie_value("trip_admin"), self.server.admin_password
        )

    def require_admin_access(self) -> None:
        if not self.has_admin_access():
            raise ApiError(401, "请先登录管理员。", {"code": "ADMIN_REQUIRED"})

    def handle_admin_write(self, method: str, path: str) -> None:
        self.enforce_rate_limit("admin_login" if path.endswith("/session") else "admin_write")
        data = self.read_json()
        if path == "/api/admin/session" and method == "POST":
            password = data.get("password", "") if isinstance(data, dict) else ""
            if not self.server.admin_password or not isinstance(password, str) or not hmac.compare_digest(token_hash(password), token_hash(self.server.admin_password)):
                raise ApiError(403, "管理员密码不正确。")
            self.send_json(200, {"authenticated": True}, extra_headers={"Set-Cookie": self.build_cookie("trip_admin", create_admin_access_token(self.server.admin_password), ADMIN_SESSION_MAX_AGE)})
            return
        if path == "/api/admin/session" and method == "DELETE":
            self.send_json(200, {"authenticated": False}, extra_headers={"Set-Cookie": self.clear_cookie("trip_admin")})
            return
        self.require_admin_access()
        if path == "/api/admin/api-settings" and method == "PUT":
            self.send_json(200, self.server.save_api_settings(data))
            return
        if path == '/api/admin/projects' and method == 'POST':
            if not isinstance(data, dict):
                raise ApiError(400, '项目资料格式不正确。')
            name = validate_project_name(data.get('name'))
            try:
                code = validate_project_code(data.get('project_code', ''), allow_empty=True)
                import place_cache
                with self.server.project_store.lock:
                    require_unique_project_code(self.server.project_store, code)
                    for project in self.server.project_store.list_projects():
                        place_cache.flush(self.server.project_store.resolve(project['id']))
                    project = self.server.project_store.create(name, code)
            except ValueError as error:
                raise ApiError(400, str(error)) from error
            self.send_json(201, {'project': project})
            return
        delete_match = re.fullmatch(r'/api/admin/projects/([^/]+)', path)
        if delete_match and method == 'DELETE':
            if not isinstance(data, dict):
                raise ApiError(400, '删除确认资料格式不正确。')
            try:
                result = self.server.delete_project(delete_match.group(1), data.get('confirm_name'))
            except KeyError as error:
                raise ApiError(404, '项目已删除或不存在。') from error
            except ValueError as error:
                raise ApiError(409, str(error)) from error
            except sqlite3.Error as error:
                raise ApiError(503, '项目资料暂时无法归档，请稍后重试。') from error
            self.send_json(200, result)
            return
        with self.server.project_store.lock:
            if path == '/api/admin/project' and method == 'PUT' and isinstance(data, dict) and data.get('project_code') and data.get('remove_password') is not True:
                try:
                    code = validate_project_code(data['project_code'])
                except ValueError as error:
                    raise ApiError(400, str(error)) from error
                require_unique_project_code(self.server.project_store, code, exclude=self.project_id)
            result = admin_change(self.db_path, method, path.removeprefix("/api/admin/"), data)
        self.send_json(200, result)

    def require_project_access(self) -> None:
        if not self.has_project_access():
            raise ApiError(
                HTTPStatus.UNAUTHORIZED,
                "请先输入项目口令。",
                {"code": "PROJECT_LOCKED"},
            )

    def build_cookie(self, base_name: str, value: str, max_age: int, *, project_id: str | None = None) -> str:
        if project_id is None:
            base_name = self.scoped_cookie_name(base_name)
        elif base_name in {'trip_session', 'trip_project'} and project_id != 'main':
            base_name = f'{base_name}_{project_id}'
        secure = self.server.cookie_secure  # type: ignore[attr-defined]
        cookie_name = f"__Host-{base_name}" if secure else base_name
        cookie = (
            f"{cookie_name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
        )
        return cookie + ("; Secure" if secure else "")

    def clear_cookie(self, base_name: str) -> str:
        base_name = self.scoped_cookie_name(base_name)
        secure = self.server.cookie_secure  # type: ignore[attr-defined]
        cookie_name = f"__Host-{base_name}" if secure else base_name
        cookie = (
            f"{cookie_name}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; "
            "Expires=Thu, 01 Jan 1970 00:00:00 GMT"
        )
        return cookie + ("; Secure" if secure else "")

    def validate_write_request(self) -> None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "只接受 application/json。")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        public_origin = self.server.public_origin  # type: ignore[attr-defined]
        if public_origin and origin != public_origin:
            raise ApiError(HTTPStatus.FORBIDDEN, "请求来源不受信任。")
        if not public_origin and origin and urlparse(origin).netloc.lower() != host.lower():
            raise ApiError(HTTPStatus.FORBIDDEN, "请求来源不受信任。")

    def client_ip(self) -> str:
        peer = self.client_address[0]
        if peer in {"127.0.0.1", "::1"}:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            if forwarded:
                try:
                    return str(ipaddress.ip_address(forwarded))
                except ValueError:
                    pass
        return peer

    def enforce_rate_limit(self, scope: str) -> None:
        ip_key = f"{scope}:ip:{self.client_ip()}"
        if scope in {"access", "admin_login"}:
            ip_limit, window_seconds = 10, 300
        elif scope == "session":
            ip_limit, window_seconds = 30, 60
        else:
            ip_limit, window_seconds = 120, 60
        if not self.server.rate_limiter.allow(  # type: ignore[attr-defined]
            ip_key, ip_limit, window_seconds
        ):
            raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "操作太频繁，请稍后再试。")
        if scope == "write":
            token = self.session_token()
            token_key = f"write:session:{token_hash(token) if token else 'anonymous'}"
            if not self.server.rate_limiter.allow(token_key, 60):  # type: ignore[attr-defined]
                raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "操作太频繁，请稍后再试。")

    def list_directory(self, path: str) -> None:
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        return None

    def translate_path(self, path: str) -> str:
        translated = Path(super().translate_path(path)).resolve()
        public_root = Path(self.directory).resolve()
        try:
            translated.relative_to(public_root)
        except ValueError:
            return str(public_root / ".not-found")
        return str(translated)


def create_server(
    host: str,
    port: int,
    *,
    public_dir: Path = DEFAULT_PUBLIC_DIR,
    data_dir: Path = DEFAULT_DATA_DIR,
    cookie_secure: bool = False,
    project_code: str,
    public_origin: str = "",
    admin_password: str = "",
    ai_api_key: str = "",
    ai_model: str = "deepseek-v4-flash-vision-exp",
) -> TripHTTPServer:
    project_code = validate_project_code(project_code, allow_empty=True)
    db_path = data_dir / "trip.db"
    init_database(db_path, project_code)
    projects = ProjectStore(db_path, init_database, verify_project_code)
    handler = partial(TripRequestHandler, directory=str(public_dir))
    httpd = TripHTTPServer(
        (host, port),
        handler,
        db_path,
        cookie_secure=cookie_secure,
        project_code=project_code,
        public_origin=public_origin,
        admin_password=admin_password,
    )
    httpd.project_store = projects
    httpd.metro_store = MetroMapStore(data_dir / 'metro_maps', bundled_dir=public_dir / 'assets' / 'metro')
    httpd._ai_api_key = ai_api_key
    httpd._ai_model = ai_model
    if ai_api_key and any(project['id'] == 'main' for project in projects.list_projects()):
        from ai_service import AIService
        try:
            httpd.ai_service = AIService(
                db_path, api_key=ai_api_key, model=ai_model,
                get_context=partial(ai_context, db_path),
                normalize_item=validate_payload,
                import_items=partial(import_ai_items, db_path),
            )
        except Exception:
            httpd.server_close()
            raise
    settings_path = data_dir / "api-settings.json"
    if settings_path.exists():
        httpd.apply_api_settings(json.loads(settings_path.read_text()))
    return httpd


def create_backup(db_path: Path, destination: Path) -> Path:
    db_path = db_path.resolve()
    destination = destination.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"源数据库不存在：{db_path}")
    if destination == db_path:
        raise ValueError("备份目标不能与源数据库相同。")
    if destination.exists():
        raise FileExistsError(f"备份目标已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    source_uri = f"file:{quote(str(db_path), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(source_uri, uri=True, timeout=10) as source, sqlite3.connect(
            temporary
        ) as target:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"备份完整性检查失败：{result}")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        for temporary_file in (
            temporary,
            Path(f"{temporary}-wal"),
            Path(f"{temporary}-shm"),
        ):
            if temporary_file.exists():
                temporary_file.unlink()
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="轻量上海旅行协作规划服务")
    parser.add_argument("--host", default=os.environ.get("TRIP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TRIP_PORT", "8000")))
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("TRIP_DATA_DIR", DEFAULT_DATA_DIR)),
    )
    parser.add_argument("--backup", type=Path, help="创建 SQLite 一致性备份后退出")
    parser.add_argument(
        "--secure-cookie",
        action="store_true",
        default=os.environ.get("TRIP_COOKIE_SECURE", "0") == "1",
        help="为身份 Cookie 添加 Secure 属性（HTTPS 部署时启用）",
    )
    return parser.parse_args()


def validate_public_origin(value: str) -> str:
    origin = value.rstrip("/")
    if not origin:
        return ""
    parsed = urlparse(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("TRIP_PUBLIC_ORIGIN 必须是形如 https://trip.example.com 的完整来源地址。")
    return origin


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    db_path = data_dir / "trip.db"
    if args.backup:
        destination = args.backup.expanduser().resolve()
        try:
            create_backup(db_path, destination)
        except (
            FileNotFoundError,
            FileExistsError,
            ValueError,
            RuntimeError,
            OSError,
            sqlite3.Error,
        ) as error:
            print(f"备份失败：{error}", file=sys.stderr)
            raise SystemExit(2) from error
        print(destination)
        return
    try:
        project_code = validate_project_code(os.environ.get("TRIP_PROJECT_CODE", ""), allow_empty=True)
        admin_password = os.environ.get("TRIP_ADMIN_PASSWORD", "")
        public_origin = validate_public_origin(os.environ.get("TRIP_PUBLIC_ORIGIN", ""))
    except ValueError as error:
        print(f"启动失败：{error}", file=sys.stderr)
        raise SystemExit(2) from error
    server = create_server(
        args.host,
        args.port,
        data_dir=data_dir,
        cookie_secure=args.secure_cookie,
        project_code=project_code,
        public_origin=public_origin,
        admin_password=admin_password,
        ai_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        ai_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash-vision-exp"),
    )
    print(f"旅游规划运行在 http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
