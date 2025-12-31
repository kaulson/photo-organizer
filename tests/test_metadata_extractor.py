"""Tests for MetadataExtractor module."""

# pylint: disable=redefined-outer-name

import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from photosort.database import Database
from photosort.extractor.exiftool import ExiftoolRunner, ExiftoolResult, ExiftoolNotFoundError
from photosort.extractor.extractor import MetadataExtractor, MetadataExtractorStats
from photosort.extractor.parser import (
    parse_exif_date,
    get_first_value,
    extract_metadata_families,
    filter_metadata_for_json,
)
from photosort.extractor.strategies import (
    FullStrategy,
    SelectiveStrategy,
    get_strategy,
    SUPPORTED_EXTENSIONS,
)


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
    extension: str | None = "jpg",
    date_path_folder: int | None = None,
    size: int = 100_000,  # Default to 100KB (above MIN_FILE_SIZE_BYTES threshold)
) -> int:
    """Insert a test file and return its ID."""
    now_unix = time.time()
    now_int = int(now_unix)
    cursor = db.conn.execute(
        """
        INSERT INTO files (scan_session_id, source_path, directory_path,
                           filename_full, filename_base, extension, size,
                           scanned_at_unix, scanned_at, date_path_folder)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            source_path,
            str(Path(source_path).parent),
            filename,
            Path(filename).stem,
            extension,
            size,
            now_unix,
            now_int,
            date_path_folder,
        ),
    )
    db.conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


class TestParseExifDate:
    """Tests for parse_exif_date function."""

    def test_standard_format(self) -> None:
        unix_ts, date_int = parse_exif_date("2023:05:14 13:45:30")
        assert date_int == 20230514
        assert unix_ts is not None

    def test_iso_format(self) -> None:
        unix_ts, date_int = parse_exif_date("2023-05-14 13:45:30")
        assert date_int == 20230514
        assert unix_ts is not None

    def test_with_timezone(self) -> None:
        unix_ts, date_int = parse_exif_date("2023:05:14 13:45:30+02:00")
        assert date_int == 20230514
        assert unix_ts is not None

    def test_none_value(self) -> None:
        unix_ts, date_int = parse_exif_date(None)
        assert unix_ts is None
        assert date_int is None

    def test_empty_string(self) -> None:
        unix_ts, date_int = parse_exif_date("")
        assert unix_ts is None
        assert date_int is None

    def test_zero_date(self) -> None:
        unix_ts, date_int = parse_exif_date("0000:00:00 00:00:00")
        assert unix_ts is None
        assert date_int is None


class TestGetFirstValue:
    """Tests for get_first_value function."""

    def test_returns_first_existing(self) -> None:
        meta = {"key1": None, "key2": "value2", "key3": "value3"}
        assert get_first_value(meta, "key1", "key2", "key3") == "value2"

    def test_returns_none_if_all_missing(self) -> None:
        meta = {"other": "value"}
        assert get_first_value(meta, "key1", "key2") is None

    def test_returns_first_key(self) -> None:
        meta = {"key1": "value1", "key2": "value2"}
        assert get_first_value(meta, "key1", "key2") == "value1"


class TestExtractMetadataFamilies:
    """Tests for extract_metadata_families function."""

    def test_extracts_families(self) -> None:
        meta = {
            "EXIF:DateTimeOriginal": "2023:05:14",
            "XMP:Rating": 3,
            "QuickTime:Duration": 45.2,
        }
        result = extract_metadata_families(meta)
        assert result == "EXIF,QuickTime,XMP"

    def test_empty_metadata(self) -> None:
        assert extract_metadata_families({}) == ""


class TestFilterMetadataForJson:
    """Tests for filter_metadata_for_json function."""

    def test_excludes_thumbnail(self) -> None:
        meta = {"EXIF:ThumbnailImage": "base64data", "EXIF:Make": "Sony"}
        result = filter_metadata_for_json(meta)
        assert "EXIF:ThumbnailImage" not in result
        assert result["EXIF:Make"] == "Sony"

    def test_excludes_binary_data(self) -> None:
        meta = {"Binary": "base64:xyz", "Normal": "value"}
        result = filter_metadata_for_json(meta)
        assert "Binary" not in result
        assert result["Normal"] == "value"

    def test_excludes_source_file(self) -> None:
        meta = {"SourceFile": "/path/to/file", "EXIF:Make": "Sony"}
        result = filter_metadata_for_json(meta)
        assert "SourceFile" not in result


class TestExtractionStrategies:
    """Tests for extraction strategies."""

    def test_supported_extensions(self) -> None:
        assert "jpg" in SUPPORTED_EXTENSIONS
        assert "arw" in SUPPORTED_EXTENSIONS
        assert "mp4" in SUPPORTED_EXTENSIONS
        assert "png" not in SUPPORTED_EXTENSIONS

    def test_get_strategy_full(self) -> None:
        strategy = get_strategy("full")
        assert strategy.name == "full"

    def test_get_strategy_selective(self) -> None:
        strategy = get_strategy("selective")
        assert strategy.name == "selective"

    def test_get_strategy_invalid(self) -> None:
        with pytest.raises(ValueError):
            get_strategy("invalid")

    def test_full_strategy_returns_all_supported(self, temp_db: Database) -> None:
        session_id = _insert_scan_session(temp_db)
        _insert_file(temp_db, session_id, "/test/photo.jpg", "photo.jpg", "jpg")
        _insert_file(temp_db, session_id, "/test/video.mp4", "video.mp4", "mp4")
        _insert_file(temp_db, session_id, "/test/doc.pdf", "doc.pdf", "pdf")

        strategy = FullStrategy()
        file_ids = strategy.get_file_ids(temp_db.conn)
        assert len(file_ids) == 2

    def test_selective_strategy_excludes_with_path_date(self, temp_db: Database) -> None:
        session_id = _insert_scan_session(temp_db)
        _insert_file(
            temp_db,
            session_id,
            "/test/with_date.jpg",
            "with_date.jpg",
            "jpg",
            date_path_folder=20230514,
        )
        _insert_file(
            temp_db, session_id, "/test/no_date.jpg", "no_date.jpg", "jpg", date_path_folder=None
        )

        strategy = SelectiveStrategy()
        file_ids = strategy.get_file_ids(temp_db.conn)
        assert len(file_ids) == 1


class TestExiftoolRunner:
    """Tests for ExiftoolRunner."""

    @patch("shutil.which")
    def test_raises_if_not_found(self, mock_which: Mock) -> None:
        mock_which.return_value = None
        with pytest.raises(ExiftoolNotFoundError):
            ExiftoolRunner()


class TestMetadataExtractor:
    """Tests for MetadataExtractor class."""

    @patch.object(ExiftoolRunner, "_check_exiftool", return_value="12.76")
    @patch.object(ExiftoolRunner, "extract_batch")
    def test_extract_all_processes_files(
        self, mock_extract: Mock, _: Mock, temp_db: Database
    ) -> None:
        session_id = _insert_scan_session(temp_db)
        # source_path in DB is relative; source_root is /test/path
        file_id = _insert_file(temp_db, session_id, "photo.jpg", "photo.jpg", "jpg")

        # Mock returns absolute path (source_root + relative_path)
        mock_extract.return_value = [
            ExiftoolResult(
                "/test/path/photo.jpg",
                {
                    "EXIF:DateTimeOriginal": "2023:05:14 13:45:30",
                    "EXIF:Make": "Sony",
                    "EXIF:Model": "ILCE-7M3",
                },
            )
        ]

        extractor = MetadataExtractor(temp_db, batch_size=10)
        stats = extractor.extract_all(strategy="full")

        assert stats.total_files == 1
        assert stats.files_extracted == 1
        assert stats.files_with_date_original == 1

        row = temp_db.conn.execute(
            "SELECT * FROM file_metadata WHERE file_id = ?", (file_id,)
        ).fetchone()
        assert row is not None
        assert row["date_original"] == 20230514
        assert row["make"] == "Sony"
        assert row["model"] == "ILCE-7M3"

    @patch.object(ExiftoolRunner, "_check_exiftool", return_value="12.76")
    @patch.object(ExiftoolRunner, "extract_batch")
    def test_handles_extraction_error(self, mock_extract: Mock, _: Mock, temp_db: Database) -> None:
        session_id = _insert_scan_session(temp_db)
        # source_path in DB is relative; source_root is /test/path
        file_id = _insert_file(temp_db, session_id, "broken.jpg", "broken.jpg", "jpg")

        # Mock returns absolute path with error
        mock_extract.return_value = [
            ExiftoolResult("/test/path/broken.jpg", {}, error="File corrupted")
        ]

        extractor = MetadataExtractor(temp_db, batch_size=10)
        stats = extractor.extract_all(strategy="full")

        assert stats.total_files == 1
        assert stats.files_failed == 1

        row = temp_db.conn.execute(
            "SELECT * FROM file_metadata WHERE file_id = ?", (file_id,)
        ).fetchone()
        assert row is not None
        assert row["extraction_error"] == "File corrupted"

    @patch.object(ExiftoolRunner, "_check_exiftool", return_value="12.76")
    @patch.object(ExiftoolRunner, "extract_batch")
    def test_skips_small_files(self, mock_extract: Mock, _: Mock, temp_db: Database) -> None:
        session_id = _insert_scan_session(temp_db)
        # Create a file that's too small (1KB, below 10KB threshold)
        small_file_id = _insert_file(
            temp_db, session_id, "small.jpg", "small.jpg", "jpg", size=1000
        )
        # Create a normal-sized file (100KB)
        normal_file_id = _insert_file(
            temp_db, session_id, "normal.jpg", "normal.jpg", "jpg", size=100_000
        )

        # Only the normal file should be sent to exiftool
        mock_extract.return_value = [
            ExiftoolResult(
                "/test/path/normal.jpg",
                {"EXIF:DateTimeOriginal": "2023:05:14 13:45:30"},
            )
        ]

        extractor = MetadataExtractor(temp_db, batch_size=10)
        stats = extractor.extract_all(strategy="full")

        assert stats.total_files == 2
        assert stats.files_skipped == 1
        assert stats.files_extracted == 1

        # Check small file was marked as skipped
        small_row = temp_db.conn.execute(
            "SELECT * FROM file_metadata WHERE file_id = ?", (small_file_id,)
        ).fetchone()
        assert small_row is not None
        assert small_row["skip_reason"] is not None
        assert "file_too_small" in small_row["skip_reason"]
        assert small_row["extraction_error"] is None

        # Check normal file was extracted
        normal_row = temp_db.conn.execute(
            "SELECT * FROM file_metadata WHERE file_id = ?", (normal_file_id,)
        ).fetchone()
        assert normal_row is not None
        assert normal_row["skip_reason"] is None
        assert normal_row["date_original"] == 20230514

    @patch.object(ExiftoolRunner, "_check_exiftool", return_value="12.76")
    def test_get_stats(self, _: Mock, temp_db: Database) -> None:
        extractor = MetadataExtractor(temp_db, batch_size=10)
        stats = extractor.get_stats()
        assert "total" in stats
        assert "success" in stats
        assert "skipped" in stats


class TestMetadataExtractorStats:
    """Tests for MetadataExtractorStats dataclass."""

    def test_default_values(self) -> None:
        stats = MetadataExtractorStats()
        assert stats.total_files == 0
        assert stats.files_extracted == 0
        assert stats.files_with_date_original == 0
        assert stats.files_with_gps == 0
        assert stats.files_failed == 0
        assert stats.files_skipped == 0
