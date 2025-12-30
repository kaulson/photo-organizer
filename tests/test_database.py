"""Tests for database module."""

from pathlib import Path

from photosort.database import Database


class TestDatabase:
    """Tests for Database class."""

    def test_creates_database_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with Database(db_path):
            assert db_path.exists()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        db_path = tmp_path / "subdir" / "nested" / "test.db"
        with Database(db_path):
            assert db_path.exists()

    def test_schema_creates_tables(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        with Database(db_path) as db:
            tables = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = {row["name"] for row in tables}

            assert "scan_sessions" in table_names
            assert "completed_directories" in table_names
            assert "files" in table_names

    def test_foreign_keys_enabled(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        with Database(db_path) as db:
            result = db.conn.execute("PRAGMA foreign_keys").fetchone()
            assert result[0] == 1

    def test_row_factory_returns_dict_like(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        with Database(db_path) as db:
            db.conn.execute(
                """
                INSERT INTO scan_sessions
                (source_root, source_drive_uuid, started_at_unix, started_at, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("/test", "uuid-123", 1234567890.0, 1234567890, "running"),
            )
            row = db.conn.execute("SELECT source_root FROM scan_sessions").fetchone()
            assert row["source_root"] == "/test"
