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
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PUBLIC_DIR = PROJECT_ROOT / "public"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
SEED_PATH = PROJECT_ROOT / "seed_data.json"
MAX_BODY_BYTES = 64 * 1024
MAX_ITEMS_PER_KIND = 250
MAX_USERS = 1000
MAX_CITIES = 50
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


def validate_project_code(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError("项目口令不能为空，最多 200 个字符，可自由设置。")
    return value


def validate_admin_password(value: Any) -> str:
    if not isinstance(value, str) or len(value) < 12 or len(value) > 200:
        raise ValueError("TRIP_ADMIN_PASSWORD 必须设置为 12–200 个字符的管理员密码。")
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
            validated = validate_payload(kind, candidate)
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
        ensure_project_records(db, initial_project_code)
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
            for kind in KINDS:
                for position, payload in enumerate(seed.get(kind, []), start=1):
                    validated = validate_payload(kind, payload)
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
        db.execute("PRAGMA optimize")


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


def validate_payload(kind: str, value: Any) -> dict[str, str]:
    if kind not in KINDS:
        raise ApiError(HTTPStatus.NOT_FOUND, "未知内容类型。")
    if not isinstance(value, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "内容格式不正确。")
    cleaned: dict[str, str] = {}
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
    return cleaned


def item_label(kind: str, payload: dict[str, Any]) -> str:
    return str(payload["title"] if kind == "itinerary" else payload["name"])


def row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "city_id": row["city_id"],
        "kind": row["kind"],
        "position": row["position"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"],
        "updated_by": row["updated_by"],
        **json.loads(row["payload"]),
    }


def snapshot(db_path: Path) -> dict[str, Any]:
    with connect_db(db_path) as db:
        db.execute("BEGIN")
        revision = db.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()["value"]
        project = db.execute(
            "SELECT project_name FROM project_settings WHERE singleton = 1"
        ).fetchone()
        city_rows = db.execute(
            "SELECT id, name, position FROM cities ORDER BY position, name COLLATE NOCASE"
        ).fetchall()
        rows = db.execute(
            "SELECT * FROM items ORDER BY city_id, kind, position, created_at, id"
        ).fetchall()
        activity_rows = db.execute(
            """
            SELECT revision, action, item_id, city_id, kind, item_name, user_id, happened_at
            FROM activity ORDER BY revision DESC LIMIT 30
            """
        ).fetchall()
    grouped = {kind: [] for kind in KINDS}
    for row in rows:
        grouped[row["kind"]].append(row_to_item(row))
    return {
        "revision": revision,
        "project": {"name": project["project_name"]},
        "cities": [dict(row) for row in city_rows],
        "items": grouped,
        "activity": [dict(row) for row in activity_rows],
        "server_time": utc_now(),
    }


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


def create_item(db_path: Path, kind: str, data: Any, user_id: str) -> dict[str, Any]:
    payload = validate_payload(kind, data)
    city_id = data.get("city_id", DEFAULT_CITY_ID)
    now = utc_now()
    item_id = uuid.uuid4().hex[:12]
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not isinstance(city_id, str) or not db.execute("SELECT 1 FROM cities WHERE id = ?", (city_id,)).fetchone():
            raise ApiError(400, "请选择有效城市。")
        item_count = db.execute(
            "SELECT COUNT(*) AS count FROM items WHERE kind = ?", (kind,)
        ).fetchone()["count"]
        if item_count >= MAX_ITEMS_PER_KIND:
            raise ApiError(HTTPStatus.INSUFFICIENT_STORAGE, "这一分类的条目已达上限，请先整理现有内容。")
        position = db.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS position FROM items WHERE kind = ?",
            (kind,),
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
        row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return {"revision": revision, "item": row_to_item(row)}


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
        if current["version"] != version:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "这条内容刚被其他人更新，请核对后再保存。",
                {"current": row_to_item(current)},
            )
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
        row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return {"revision": revision, "item": row_to_item(row)}


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
            if code:
                try:
                    validate_project_code(code)
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
                db.execute("DELETE FROM users WHERE user_id=?", (old,))
            else:
                raise ApiError(405, "操作不支持。")
        elif resource == "cities":
            if method == "POST":
                name = validate_city_name(data.get("name"))
                if db.execute("SELECT COUNT(*) FROM cities").fetchone()[0] >= MAX_CITIES:
                    raise ApiError(400, "城市数量已达上限。")
                if db.execute("SELECT 1 FROM cities WHERE name=?", (name,)).fetchone():
                    raise ApiError(409, "城市已存在。")
                db.execute("INSERT INTO cities VALUES (?, ?, (SELECT COALESCE(MAX(position),0)+1 FROM cities), ?, ?)", (uuid.uuid4().hex[:12], name, now, now))
            elif method in {"PUT", "DELETE"}:
                city_id = data.get("id")
                if not isinstance(city_id, str) or not db.execute("SELECT 1 FROM cities WHERE id=?", (city_id,)).fetchone():
                    raise ApiError(404, "城市不存在。")
                if method == "PUT":
                    name = validate_city_name(data.get("name"))
                    if db.execute("SELECT 1 FROM cities WHERE name=? AND id<>?", (name, city_id)).fetchone():
                        raise ApiError(409, "城市已存在。")
                    db.execute("UPDATE cities SET name=?, updated_at=? WHERE id=?", (name, now, city_id))
                else:
                    if db.execute("SELECT COUNT(*) FROM cities").fetchone()[0] <= 1:
                        raise ApiError(409, "至少保留一个城市。")
                    if db.execute("SELECT 1 FROM items WHERE city_id=?", (city_id,)).fetchone():
                        raise ApiError(409, "城市中仍有内容，请先整理后再删除。")
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
        super().__init__(server_address, handler)
        self.db_path = db_path
        self.cookie_secure = cookie_secure
        self.project_code = project_code
        self.admin_password = admin_password
        self.public_origin = public_origin.rstrip("/")
        self.rate_limiter = SlidingWindowLimiter()

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
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        if not urlparse(self.path).path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    @property
    def db_path(self) -> Path:
        return self.server.db_path  # type: ignore[attr-defined]

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
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "一次提交的内容不能超过 64 KB。")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except TimeoutError as exc:
            raise ApiError(HTTPStatus.REQUEST_TIMEOUT, "请求读取超时，请重试。") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 内容格式不正确。") from exc

    def handle_api_error(self, error: Exception) -> None:
        if isinstance(error, ApiError):
            self.send_json(error.status, {"error": error.message, **error.extra})
            return
        print(f"Unhandled API error: {error}", file=sys.stderr)
        self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "服务器暂时无法完成请求。"})

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            if not path.startswith("/api/"):
                super().do_GET()
                return
            if path == "/api/health":
                with connect_db(self.db_path) as db:
                    revision = db.execute(
                        "SELECT value FROM meta WHERE key = 'revision'"
                    ).fetchone()["value"]
                self.send_json(
                    HTTPStatus.OK,
                    {"status": "ok", "revision": revision, "time": utc_now()},
                )
                return
            if path == "/api/project-session":
                self.send_json(
                    HTTPStatus.OK,
                    {"unlocked": self.has_project_access()},
                )
                return
            if path == "/api/config":
                self.send_json(HTTPStatus.OK, {"project_code_required": True})
                return
            if path == "/api/admin/session":
                self.send_json(200, {"authenticated": self.has_admin_access(), "enabled": bool(self.server.admin_password)})
                return
            if path.startswith("/api/admin/"):
                self.require_admin_access()
                if path != "/api/admin/state":
                    raise ApiError(404, "接口不存在。")
                self.send_json(200, admin_state(self.db_path))
                return
            self.require_project_access()
            if path == "/api/users":
                self.send_json(HTTPStatus.OK, {"users": list_users(self.db_path)})
                return
            if path == "/api/session":
                user_id = authenticate(self.db_path, self.session_token())
                self.send_json(HTTPStatus.OK, {"user_id": user_id})
                return
            if path == "/api/snapshot":
                state = snapshot(self.db_path)
                etag = f'"revision-{state["revision"]}"'
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
            if path == "/api/project-session":
                self.enforce_rate_limit("access")
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
            self.enforce_rate_limit("session" if path == "/api/session" else "write")
            data = self.read_json()
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
            match = re.fullmatch(r"/api/items/(attraction|transit|food|itinerary)", path)
            if not match:
                raise ApiError(HTTPStatus.NOT_FOUND, "接口不存在。")
            user_id = authenticate(self.db_path, self.session_token())
            result = create_item(self.db_path, match.group(1), data, user_id)
            self.send_json(HTTPStatus.CREATED, result)
        except Exception as error:
            self.handle_api_error(error)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            self.validate_write_request()
            if urlparse(self.path).path.startswith("/api/admin/"):
                self.handle_admin_write("PUT", urlparse(self.path).path)
                return
            self.require_project_access()
            self.enforce_rate_limit("write")
            path = urlparse(self.path).path
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
                self.enforce_rate_limit("access")
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
        print(f"{self.address_string()} [{self.log_date_time_string()}] {fmt % args}", file=sys.stderr)

    def cookie_value(self, base_name: str) -> str:
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
        self.send_json(200, admin_change(self.db_path, method, path.removeprefix("/api/admin/"), data))

    def require_project_access(self) -> None:
        if not self.has_project_access():
            raise ApiError(
                HTTPStatus.UNAUTHORIZED,
                "请先输入项目口令。",
                {"code": "PROJECT_LOCKED"},
            )

    def build_cookie(self, base_name: str, value: str, max_age: int) -> str:
        secure = self.server.cookie_secure  # type: ignore[attr-defined]
        cookie_name = f"__Host-{base_name}" if secure else base_name
        cookie = (
            f"{cookie_name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
        )
        return cookie + ("; Secure" if secure else "")

    def clear_cookie(self, base_name: str) -> str:
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
) -> TripHTTPServer:
    project_code = validate_project_code(project_code)
    db_path = data_dir / "trip.db"
    if admin_password:
        validate_admin_password(admin_password)
    init_database(db_path, project_code)
    handler = partial(TripRequestHandler, directory=str(public_dir))
    return TripHTTPServer(
        (host, port),
        handler,
        db_path,
        cookie_secure=cookie_secure,
        project_code=project_code,
        public_origin=public_origin,
        admin_password=admin_password,
    )


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
        project_code = validate_project_code(os.environ.get("TRIP_PROJECT_CODE", ""))
        admin_password = validate_admin_password(os.environ.get("TRIP_ADMIN_PASSWORD", ""))
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
