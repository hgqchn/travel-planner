from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import backup_all


class BackupAllTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def database(self, filename, value):
        path = self.data_dir / filename
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE records (value TEXT NOT NULL)")
            db.execute("INSERT INTO records VALUES (?)", (value,))
        return path

    def register(self, project_id, filename):
        with sqlite3.connect(self.data_dir / "trip.db") as db:
            db.execute("CREATE TABLE IF NOT EXISTS project_registry (id TEXT PRIMARY KEY, filename TEXT NOT NULL UNIQUE)")
            db.execute("INSERT OR IGNORE INTO project_registry VALUES ('main', 'trip.db')")
            db.execute("INSERT INTO project_registry VALUES (?, ?)", (project_id, filename))

    def assert_valid_copy(self, path, value):
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT value FROM records").fetchone()[0], value)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_registered_collection_cache_and_outbox_are_preserved(self):
        project_id = "0123456789abcdef"
        child_name = f"project-{project_id}.db"
        self.database("trip.db", "main")
        self.database(child_name, "child")
        self.database("place_cache.db", "shared cache")
        self.database("unregistered.db", "not in project collection")
        self.register(project_id, child_name)
        with sqlite3.connect(self.data_dir / child_name) as db:
            db.execute("CREATE TABLE cache_outbox (id INTEGER PRIMARY KEY, payload TEXT)")
            db.execute("INSERT INTO cache_outbox VALUES (1, 'pending place update')")
        originals = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in self.data_dir.glob("*.db")}
        output = self.root / "backups" / "all-projects"
        self.assertEqual(backup_all.backup_all(self.data_dir, output), output)
        self.assertEqual({path.name for path in output.iterdir()}, {"trip.db", child_name, "place_cache.db"})
        for filename, value in (("trip.db", "main"), (child_name, "child"), ("place_cache.db", "shared cache")):
            self.assert_valid_copy(output / filename, value)
        with sqlite3.connect(output / child_name) as db:
            self.assertEqual(db.execute("SELECT payload FROM cache_outbox").fetchone()[0], "pending place update")
        self.assertEqual({path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in self.data_dir.glob("*.db")}, originals)
        self.assertEqual(list(output.parent.iterdir()), [output])

    def test_existing_output_rejected_and_invalid_collection_cleans_up(self):
        self.database("trip.db", "main")
        output = self.root / "existing"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("keep original backup", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            backup_all.backup_all(self.data_dir, output)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep original backup")
        self.assertEqual(list(output.iterdir()), [sentinel])
        project_id = "0123456789abcdef"
        self.register(project_id, "../outside.db")
        for filename, expected in (("../outside.db", ValueError),
                                   (f"project-{project_id}.db", FileNotFoundError)):
            with self.subTest(filename=filename):
                with sqlite3.connect(self.data_dir / "trip.db") as db:
                    db.execute("UPDATE project_registry SET filename=? WHERE id=?", (filename, project_id))
                failed_output = self.root / "failed"
                with self.assertRaises(expected):
                    backup_all.backup_all(self.data_dir, failed_output)
                self.assertFalse(failed_output.exists())
                self.assertEqual({path.name for path in self.root.iterdir()}, {"data", "existing"})

    def test_legacy_database_without_registry_or_cache_is_supported(self):
        self.database("trip.db", "legacy project")
        # An unrelated file must not silently become part of a legacy backup.
        self.database("project-0123456789abcdef.db", "unregistered child")
        output = self.root / "legacy-backup"
        backup_all.backup_all(self.data_dir, output)
        self.assertEqual([path.name for path in output.iterdir()], ["trip.db"])
        self.assert_valid_copy(output / "trip.db", "legacy project")


if __name__ == "__main__":
    unittest.main()
