"""A deployment's projects use separate SQLite files and share its place cache."""
from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class ProjectStore:
    def __init__(self, default_db: Path, initialize: Callable):
        self.default_db = default_db
        self.initialize = initialize
        self.lock = threading.RLock()
        with sqlite3.connect(default_db) as db:
            db.execute("CREATE TABLE IF NOT EXISTS project_registry (id TEXT PRIMARY KEY, filename TEXT NOT NULL UNIQUE, deleted_at TEXT)")
            if 'deleted_at' not in {row[1] for row in db.execute('PRAGMA table_info(project_registry)')}:
                db.execute('ALTER TABLE project_registry ADD COLUMN deleted_at TEXT')
            db.execute("INSERT OR IGNORE INTO project_registry(id,filename) VALUES ('main', ?)", (default_db.name,))
        # Upgrade every existing project before accepting requests.
        for project in self.list_projects():
            if project['id'] != 'main':
                self.initialize(self.resolve(project['id']), 'unused-existing-project', empty_project=True)
            # No worker exists yet when a deployment starts. A stale queued job
            # must not prevent project deletion forever after a process restart.
            with sqlite3.connect(self.resolve(project['id'])) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_jobs'").fetchone():
                    db.execute("UPDATE ai_jobs SET status='failed',error=?,updated_at=? WHERE status IN ('queued','running')",
                               ('服务已重启，上次生成未完成；请重新生成。', datetime.now(timezone.utc).isoformat()))

    def resolve(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not re.fullmatch(r'main|[0-9a-f]{16}', project_id):
            raise KeyError('项目不存在。')
        with sqlite3.connect(self.default_db) as db:
            row = db.execute('SELECT filename FROM project_registry WHERE id=? AND deleted_at IS NULL', (project_id,)).fetchone()
        expected = self.default_db.name if project_id == 'main' else f'project-{project_id}.db'
        if not row or row[0] != expected:
            raise KeyError('项目不存在。')
        path = self.default_db.parent / expected
        if not path.is_file():
            raise KeyError('项目数据文件不存在。')
        return path

    def list_projects(self) -> list[dict[str, str]]:
        with self.lock:
            with sqlite3.connect(self.default_db) as db:
                rows = db.execute('SELECT id FROM project_registry WHERE deleted_at IS NULL ORDER BY rowid').fetchall()
            result = []
            for (project_id,) in rows:
                path = self.resolve(project_id)
                with sqlite3.connect(path) as db:
                    name = db.execute('SELECT project_name FROM project_settings WHERE singleton=1').fetchone()[0]
                result.append({'id': project_id, 'name': name, 'url': '/' if project_id == 'main' else f'/?project={project_id}'})
            return result

    def create(self, name: str, code: str) -> dict[str, str]:
        with self.lock:
            if len(self.list_projects()) >= 20:
                raise ValueError('最多创建 20 个项目。')
            project_id = uuid.uuid4().hex[:16]
            path = self.default_db.parent / f'project-{project_id}.db'
            self.initialize(path, code, empty_project=True)
            with sqlite3.connect(path) as db:
                db.execute('UPDATE project_settings SET project_name=? WHERE singleton=1', (name,))
            with sqlite3.connect(self.default_db) as db:
                db.execute('INSERT INTO project_registry(id,filename) VALUES (?,?)', (project_id, path.name))
            return {'id': project_id, 'name': name, 'url': f'/?project={project_id}'}

    def delete(self, project_id: str, confirm_name: str) -> dict:
        """Remove access and listing atomically; retain the database as an archive."""
        import place_cache
        with self.lock:
            path = self.resolve(project_id)
            with sqlite3.connect(path) as db:
                name = db.execute('SELECT project_name FROM project_settings WHERE singleton=1').fetchone()[0]
                if not isinstance(confirm_name, str) or confirm_name != name:
                    raise ValueError('项目名称不一致。请刷新列表，并输入完整项目名称确认删除。')
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_jobs'").fetchone():
                    if db.execute("SELECT 1 FROM ai_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                        raise ValueError('该项目正在生成 AI 内容，请等待生成结束后再删除。')
            # Cache failures abort deletion while the durable queue remains intact.
            place_cache.flush(path)
            with sqlite3.connect(self.default_db) as db:
                cursor = db.execute('UPDATE project_registry SET deleted_at=? WHERE id=? AND deleted_at IS NULL',
                                    (datetime.now(timezone.utc).isoformat(), project_id))
                if not cursor.rowcount:
                    raise KeyError('项目已删除或不存在。')
            projects = self.list_projects()
            return {'deleted_id': project_id, 'projects': projects,
                    'next_project_id': projects[0]['id'] if projects else None}
