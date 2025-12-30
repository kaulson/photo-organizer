"""Tests for DateResolver class."""

# pylint: disable=redefined-outer-name

import tempfile
import time
from pathlib import Path

import pytest

from photosort.database import Database
from photosort.resolver.resolver import DateResolver


@pytest.fixture
def temp_db() -> Database:
    """Create a temporary database with schema for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    db = Database(db_path)
    db.connect()
    return db


def _insert_scan_session(db: Database) -> int:
    """Insert a test scan session and return its ID."""
    now_unix = time.time()
    now_int = int(now_unix)
    cursor = db.conn.execute(
        """
        INSERT INTO scan_sessions (source_root, source_drive_uuid,
                                   started_at_unix, started_at, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("/test/path", "test-uuid", now_unix, now_int, "completed"),
    )
    db.conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def _insert_file(
    db: Database,
    session_id: int,
    source_path: str,
    filename: str,
    size: int = 1000,
) -> None:
    """Insert a test file."""
    now_unix = time.time()
    now_int = int(now_unix)
    db.conn.execute(
        """
        INSERT INTO files (scan_session_id, source_path, directory_path,
                           filename_full, filename_base, extension, size,
                           scanned_at_unix, scanned_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            source_path,
            str(Path(source_path).parent),
            filename,
            Path(filename).stem,
            Path(filename).suffix or None,
            size,
            now_unix,
            now_int,
        ),
    )


@pytest.fixture
def seeded_db(temp_db: Database) -> Database:
    """Database with test files inserted."""
    session_id = _insert_scan_session(temp_db)

    files = [
        # Hierarchy date
        ("2023/05/14/photo1.jpg", "photo1.jpg"),
        # Folder date
        ("photos/20230601-vacation/sunset.jpg", "sunset.jpg"),
        # Filename date
        ("random/folder/IMG_20231225_143052.jpg", "IMG_20231225_143052.jpg"),
        # No date
        ("random/folder/nodate.jpg", "nodate.jpg"),
        # Multiple strategies (hierarchy wins)
        ("2023/08/15/20230101-event/IMG_20220101.jpg", "IMG_20220101.jpg"),
    ]

    for source_path, filename in files:
        _insert_file(temp_db, session_id, source_path, filename)

    temp_db.conn.commit()
    return temp_db


class TestDateResolver:
    """Tests for DateResolver class."""

    def test_resolve_hierarchy_date(self, seeded_db: Database) -> None:
        resolver = DateResolver(seeded_db, batch_size=10)
        resolver.resolve_all()

        row = seeded_db.conn.execute(
            "SELECT * FROM files WHERE filename_full = 'photo1.jpg'"
        ).fetchone()

        assert row["date_path_hierarchy"] == 20230514
        assert row["date_path_resolved"] == 20230514
        assert row["date_resolved_source"] == "hierarchy"

    def test_resolve_folder_date(self, seeded_db: Database) -> None:
        resolver = DateResolver(seeded_db)
        resolver.resolve_all()

        row = seeded_db.conn.execute(
            "SELECT * FROM files WHERE filename_full = 'sunset.jpg'"
        ).fetchone()

        assert row["date_path_folder"] == 20230601
        assert row["date_path_resolved"] == 20230601
        assert row["date_resolved_source"] == "folder"

    def test_resolve_filename_date(self, seeded_db: Database) -> None:
        resolver = DateResolver(seeded_db)
        resolver.resolve_all()

        row = seeded_db.conn.execute(
            "SELECT * FROM files WHERE filename_full = 'IMG_20231225_143052.jpg'"
        ).fetchone()

        assert row["date_path_filename"] == 20231225
        assert row["date_path_resolved"] == 20231225
        assert row["date_resolved_source"] == "filename"

    def test_no_date_resolved(self, seeded_db: Database) -> None:
        resolver = DateResolver(seeded_db)
        resolver.resolve_all()

        row = seeded_db.conn.execute(
            "SELECT * FROM files WHERE filename_full = 'nodate.jpg'"
        ).fetchone()

        assert row["date_path_resolved"] is None
        assert row["date_resolved_source"] is None
        assert row["date_resolved_at_unix"] is not None

    def test_hierarchy_takes_priority(self, seeded_db: Database) -> None:
        resolver = DateResolver(seeded_db)
        resolver.resolve_all()

        row = seeded_db.conn.execute(
            "SELECT * FROM files WHERE source_path LIKE '%2023/08/15%'"
        ).fetchone()

        assert row["date_path_hierarchy"] == 20230815
        assert row["date_path_folder"] == 20230101
        assert row["date_path_filename"] == 20220101
        assert row["date_path_resolved"] == 20230815
        assert row["date_resolved_source"] == "hierarchy"

    def test_stats_accuracy(self, seeded_db: Database) -> None:
        resolver = DateResolver(seeded_db)
        stats = resolver.resolve_all()

        assert stats.total_files == 5
        assert stats.files_with_hierarchy == 2
        assert stats.files_with_folder == 2
        assert stats.files_with_filename == 2
        assert stats.files_resolved == 4

    def test_reprocess_updates_all(self, seeded_db: Database) -> None:
        resolver = DateResolver(seeded_db)

        resolver.resolve_all()

        stats = resolver.resolve_all(reprocess=False)
        assert stats.total_files == 0

        stats = resolver.resolve_all(reprocess=True)
        assert stats.total_files == 5

    def test_batch_processing(self, temp_db: Database) -> None:
        """Test that batch processing handles multiple batches correctly."""
        session_id = _insert_scan_session(temp_db)

        # Insert 55 files with hierarchy dates
        for i in range(55):
            day = (i % 28) + 1
            _insert_file(
                temp_db,
                session_id,
                f"2023/01/{day:02d}/file_{i}.jpg",
                f"file_{i}.jpg",
            )
        temp_db.conn.commit()

        resolver = DateResolver(temp_db, batch_size=10)
        stats = resolver.resolve_all()

        assert stats.total_files == 55
