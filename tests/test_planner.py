"""Tests for Planner module.

This test module is organized to test the planner pipeline in a bottom-up manner:
1. File date resolution (priority hierarchy)
2. Folder analysis (statistics calculation)
3. Folder resolution (applying rules/thresholds)
4. Target path construction
5. Round-trip integration tests

Each section uses clear, atomic test data that makes it easy to reason about
the expected behavior based on the input.
"""

# pylint: disable=redefined-outer-name
# pylint: disable=import-outside-toplevel
# pylint: disable=unused-argument
# pylint: disable=line-too-long

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from photosort.database import Database


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def temp_db():
    """Create a temporary database with schema for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    db = Database(db_path)
    db.connect()
    yield db
    db.close()


@dataclass
class FileData:
    """Helper to define test file data clearly."""

    source_path: str
    extension: str | None = "jpg"
    size: int = 100_000
    # Path-based dates (from PathDateExtractor)
    date_path_folder: int | None = None
    date_path_filename: int | None = None
    # Filesystem dates (unix timestamp)
    fs_modified_at_unix: float | None = None

    @property
    def directory_path(self) -> str:
        return str(Path(self.source_path).parent)

    @property
    def filename_full(self) -> str:
        return Path(self.source_path).name

    @property
    def filename_base(self) -> str:
        return Path(self.source_path).stem


@dataclass
class MetadataData:
    """Helper to define test metadata (EXIF) data clearly."""

    file_id: int
    date_original: int | None = None
    make: str | None = None
    model: str | None = None


def insert_scan_session(db: Database, source_root: str = "/test/source") -> int:
    """Insert a test scan session and return its ID."""
    now_unix = time.time()
    now_int = int(now_unix)
    cursor = db.conn.execute(
        """
        INSERT INTO scan_sessions (source_root, source_drive_uuid,
                                   started_at_unix, started_at, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source_root, "test-uuid", now_unix, now_int, "completed"),
    )
    db.conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def insert_file(db: Database, session_id: int, file: FileData) -> int:
    """Insert a test file and return its ID."""
    now_unix = time.time()
    now_int = int(now_unix)
    cursor = db.conn.execute(
        """
        INSERT INTO files (
            scan_session_id, source_path, directory_path,
            filename_full, filename_base, extension, size,
            scanned_at_unix, scanned_at,
            date_path_folder, date_path_filename, fs_modified_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            file.source_path,
            file.directory_path,
            file.filename_full,
            file.filename_base,
            file.extension,
            file.size,
            now_unix,
            now_int,
            file.date_path_folder,
            file.date_path_filename,
            file.fs_modified_at_unix,
        ),
    )
    db.conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def insert_metadata(db: Database, metadata: MetadataData) -> int:
    """Insert test file metadata and return its ID."""
    now_unix = time.time()
    now_int = int(now_unix)
    cursor = db.conn.execute(
        """
        INSERT INTO file_metadata (
            file_id, date_original, make, model,
            extracted_at_unix, extracted_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            metadata.file_id,
            metadata.date_original,
            metadata.make,
            metadata.model,
            now_unix,
            now_int,
        ),
    )
    db.conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def insert_files_and_metadata(
    db: Database, session_id: int, files: list[FileData], metadata_list: list[MetadataData] | None
) -> list[int]:
    """Insert multiple files and optionally their metadata, returning file IDs."""
    file_ids = []
    for file in files:
        file_id = insert_file(db, session_id, file)
        file_ids.append(file_id)

    if metadata_list:
        for metadata in metadata_list:
            insert_metadata(db, metadata)

    return file_ids


# =============================================================================
# Tests: File Date Resolution
# =============================================================================


class TestFileDateResolution:
    """Tests for resolve_file_date() - the priority hierarchy.

    Priority order: path_folder > path_filename > exif > fs_modified
    """

    def test_path_folder_has_highest_priority(self, temp_db: Database) -> None:
        """When path_folder date exists, it takes precedence over all others."""
        from photosort.planner.resolver import resolve_file_date

        # File with all date sources available
        date_path_folder = 20231015
        date_path_filename = 20230901
        fs_modified_at_unix = 1609459200.0  # 2021-01-01
        exif_date = 20220505

        result = resolve_file_date(
            date_path_folder=date_path_folder,
            date_path_filename=date_path_filename,
            date_exif=exif_date,
            fs_modified_unix=fs_modified_at_unix,
        )

        assert result.date == 20231015
        assert result.source == "path_folder"

    def test_path_filename_when_no_path_folder(self, temp_db: Database) -> None:
        """When path_folder is None, path_filename takes precedence."""
        from photosort.planner.resolver import resolve_file_date

        result = resolve_file_date(
            date_path_folder=None,
            date_path_filename=20230901,
            date_exif=20220505,
            fs_modified_unix=1609459200.0,  # 2021-01-01
        )

        assert result.date == 20230901
        assert result.source == "path_filename"

    def test_exif_when_no_path_dates(self, temp_db: Database) -> None:
        """When no path dates exist, EXIF date is used."""
        from photosort.planner.resolver import resolve_file_date

        result = resolve_file_date(
            date_path_folder=None,
            date_path_filename=None,
            date_exif=20220505,
            fs_modified_unix=1609459200.0,  # 2021-01-01
        )

        assert result.date == 20220505
        assert result.source == "exif"

    def test_fs_modified_as_fallback(self, temp_db: Database) -> None:
        """When no other dates exist, fs_modified is used as fallback."""
        from photosort.planner.resolver import resolve_file_date

        # 2021-01-01 00:00:00 UTC
        fs_modified_unix = 1609459200.0

        result = resolve_file_date(
            date_path_folder=None,
            date_path_filename=None,
            date_exif=None,
            fs_modified_unix=fs_modified_unix,
        )

        assert result.date == 20210101
        assert result.source == "fs_modified"

    def test_no_date_available(self, temp_db: Database) -> None:
        """When no date sources are available, result is None."""
        from photosort.planner.resolver import resolve_file_date

        result = resolve_file_date(
            date_path_folder=None,
            date_path_filename=None,
            date_exif=None,
            fs_modified_unix=None,
        )

        assert result.date is None
        assert result.source == "none"

    def test_unix_timestamp_to_date_conversion(self, temp_db: Database) -> None:
        """Verify correct conversion of Unix timestamp to YYYYMMDD."""
        from photosort.planner.resolver import resolve_file_date

        test_cases = [
            (1609459200.0, 20210101),  # 2021-01-01 00:00:00 UTC
            (1672531200.0, 20230101),  # 2023-01-01 00:00:00 UTC
            (1704067200.0, 20240101),  # 2024-01-01 00:00:00 UTC
        ]

        for unix_ts, expected_date in test_cases:
            result = resolve_file_date(
                date_path_folder=None,
                date_path_filename=None,
                date_exif=None,
                fs_modified_unix=unix_ts,
            )
            assert result.date == expected_date, f"Failed for timestamp {unix_ts}"


# =============================================================================
# Tests: Folder Analysis
# =============================================================================


class TestFolderAnalysis:
    """Tests for analyze_folder() - computing folder statistics."""

    def test_basic_folder_stats(self, temp_db: Database) -> None:
        """Calculate basic folder statistics."""
        from photosort.planner.analyzer import analyze_folder

        # Folder with 3 images, all with dates
        files_data = [
            {"date": 20231015, "is_image": True},
            {"date": 20231015, "is_image": True},
            {"date": 20231016, "is_image": True},
        ]

        analysis = analyze_folder(files_data)

        assert analysis.total_files == 3
        assert analysis.image_files == 3
        assert analysis.images_with_date == 3
        assert analysis.date_coverage_pct == 1.0  # 100%

    def test_date_coverage_calculation(self, temp_db: Database) -> None:
        """Coverage is images_with_date / image_files."""
        from photosort.planner.analyzer import analyze_folder
        from typing import Any

        # 4 images: 2 with dates, 2 without
        files_data: list[dict[str, Any]] = [
            {"date": 20231015, "is_image": True},
            {"date": 20231015, "is_image": True},
            {"date": None, "is_image": True},
            {"date": None, "is_image": True},
        ]

        analysis = analyze_folder(files_data)

        assert analysis.image_files == 4
        assert analysis.images_with_date == 2
        assert analysis.date_coverage_pct == 0.5  # 50%

    def test_prevalent_date_calculation(self, temp_db: Database) -> None:
        """Find the most common date and its percentage."""
        from photosort.planner.analyzer import analyze_folder

        # 5 images: 4 on same date, 1 different
        files_data = [
            {"date": 20231015, "is_image": True},
            {"date": 20231015, "is_image": True},
            {"date": 20231015, "is_image": True},
            {"date": 20231015, "is_image": True},
            {"date": 20231020, "is_image": True},
        ]

        analysis = analyze_folder(files_data)

        assert analysis.prevalent_date == 20231015
        assert analysis.prevalent_date_count == 4
        assert analysis.prevalent_date_pct == 0.8  # 80%

    def test_date_span_calculation(self, temp_db: Database) -> None:
        """Calculate span in calendar months between min and max dates."""
        from photosort.planner.analyzer import analyze_folder

        # Images spanning from Jan 2023 to Apr 2023 (4 months)
        files_data = [
            {"date": 20230115, "is_image": True},
            {"date": 20230220, "is_image": True},
            {"date": 20230410, "is_image": True},
        ]

        analysis = analyze_folder(files_data)

        assert analysis.min_date == 20230115
        assert analysis.max_date == 20230410
        assert analysis.date_span_months == 3  # Jan, Feb, Mar, Apr = 3 months span

    def test_date_span_same_month(self, temp_db: Database) -> None:
        """Span is 0 when all dates are in same month."""
        from photosort.planner.analyzer import analyze_folder

        files_data = [
            {"date": 20231001, "is_image": True},
            {"date": 20231015, "is_image": True},
            {"date": 20231031, "is_image": True},
        ]

        analysis = analyze_folder(files_data)

        assert analysis.date_span_months == 0  # All in same month

    def test_mixed_media_types(self, temp_db: Database) -> None:
        """Non-image files don't count in image statistics."""
        from photosort.planner.analyzer import analyze_folder
        from typing import Any

        files_data: list[dict[str, Any]] = [
            {"date": 20231015, "is_image": True},
            {"date": 20231015, "is_image": False},  # Non-image (e.g., .txt)
            {"date": None, "is_image": False},  # Non-image without date
        ]

        analysis = analyze_folder(files_data)

        assert analysis.total_files == 3
        assert analysis.image_files == 1
        assert analysis.images_with_date == 1
        assert analysis.date_coverage_pct == 1.0  # 1/1 = 100%

    def test_no_images_in_folder(self, temp_db: Database) -> None:
        """Folder with no images."""
        from photosort.planner.analyzer import analyze_folder

        files_data = [
            {"date": None, "is_image": False},
            {"date": None, "is_image": False},
        ]

        analysis = analyze_folder(files_data)

        assert analysis.total_files == 2
        assert analysis.image_files == 0
        assert analysis.images_with_date == 0
        assert analysis.date_coverage_pct == 0.0

    def test_unique_date_count(self, temp_db: Database) -> None:
        """Count distinct dates in folder."""
        from photosort.planner.analyzer import analyze_folder

        files_data = [
            {"date": 20231015, "is_image": True},
            {"date": 20231015, "is_image": True},
            {"date": 20231016, "is_image": True},
            {"date": 20231020, "is_image": True},
        ]

        analysis = analyze_folder(files_data)

        assert analysis.unique_date_count == 3  # 20231015, 20231016, 20231020


# =============================================================================
# Tests: Folder Resolution Rules
# =============================================================================


class TestFolderResolution:
    """Tests for resolve_folder() - applying rules and thresholds.

    Default thresholds:
    - min_coverage_threshold: 0.30 (30%)
    - min_prevalence_threshold: 0.80 (80%)
    - max_date_span_months: 3
    """

    def test_no_images_goes_to_non_media_bucket(self, temp_db: Database) -> None:
        """Folders with no images go to _non_media bucket."""
        from photosort.planner.analyzer import FolderDateAnalysis
        from photosort.planner.resolver import resolve_folder, PlannerConfig

        analysis = FolderDateAnalysis(
            total_files=5,
            image_files=0,
            images_with_date=0,
            date_coverage_pct=0.0,
            prevalent_date=None,
            prevalent_date_count=0,
            prevalent_date_pct=0.0,
            min_date=None,
            max_date=None,
            date_span_months=0,
            unique_date_count=0,
        )

        config = PlannerConfig()
        result = resolve_folder(analysis, config)

        assert result.bucket == "non_media"
        assert result.resolved_date is None
        assert result.source == "no_images"

    def test_low_coverage_goes_to_mixed_dates(self, temp_db: Database) -> None:
        """Folders with <30% date coverage go to _mixed_dates bucket."""
        from photosort.planner.analyzer import FolderDateAnalysis
        from photosort.planner.resolver import resolve_folder, PlannerConfig

        # 10 images, only 2 with dates (20% coverage)
        analysis = FolderDateAnalysis(
            total_files=10,
            image_files=10,
            images_with_date=2,
            date_coverage_pct=0.20,  # Below 30% threshold
            prevalent_date=20231015,
            prevalent_date_count=2,
            prevalent_date_pct=1.0,
            min_date=20231015,
            max_date=20231015,
            date_span_months=0,
            unique_date_count=1,
        )

        config = PlannerConfig()
        result = resolve_folder(analysis, config)

        assert result.bucket == "mixed_dates"
        assert result.source == "low_coverage"

    def test_wide_date_spread_goes_to_mixed_dates(self, temp_db: Database) -> None:
        """Folders with date spread >= 3 months go to _mixed_dates bucket."""
        from photosort.planner.analyzer import FolderDateAnalysis
        from photosort.planner.resolver import resolve_folder, PlannerConfig

        # Good coverage but dates span 4 months
        analysis = FolderDateAnalysis(
            total_files=10,
            image_files=10,
            images_with_date=10,
            date_coverage_pct=1.0,
            prevalent_date=20230115,
            prevalent_date_count=5,
            prevalent_date_pct=0.5,
            min_date=20230115,
            max_date=20230515,  # 4 months span
            date_span_months=4,  # >= 3 months threshold
            unique_date_count=10,
        )

        config = PlannerConfig()
        result = resolve_folder(analysis, config)

        assert result.bucket == "mixed_dates"
        assert result.source == "wide_spread"

    def test_high_prevalence_resolves_to_date(self, temp_db: Database) -> None:
        """Folders with >= 80% agreement on one date resolve to that date."""
        from photosort.planner.analyzer import FolderDateAnalysis
        from photosort.planner.resolver import resolve_folder, PlannerConfig

        # 10 images, 8 on same date (80% prevalence)
        analysis = FolderDateAnalysis(
            total_files=10,
            image_files=10,
            images_with_date=10,
            date_coverage_pct=1.0,
            prevalent_date=20231015,
            prevalent_date_count=8,
            prevalent_date_pct=0.80,  # Exactly 80% threshold
            min_date=20231010,
            max_date=20231020,
            date_span_months=0,
            unique_date_count=3,
        )

        config = PlannerConfig()
        result = resolve_folder(analysis, config)

        assert result.bucket is None
        assert result.resolved_date == 20231015
        assert result.source == "prevalent_date"

    def test_unanimous_date_resolves(self, temp_db: Database) -> None:
        """Folders with 100% agreement (one unique date) resolve."""
        from photosort.planner.analyzer import FolderDateAnalysis
        from photosort.planner.resolver import resolve_folder, PlannerConfig

        # All images on same date
        analysis = FolderDateAnalysis(
            total_files=5,
            image_files=5,
            images_with_date=5,
            date_coverage_pct=1.0,
            prevalent_date=20231015,
            prevalent_date_count=5,
            prevalent_date_pct=1.0,
            min_date=20231015,
            max_date=20231015,
            date_span_months=0,
            unique_date_count=1,  # Only one unique date
        )

        config = PlannerConfig()
        result = resolve_folder(analysis, config)

        assert result.bucket is None
        assert result.resolved_date == 20231015
        assert result.source in ("prevalent_date", "unanimous")

    def test_no_consensus_goes_to_mixed_dates(self, temp_db: Database) -> None:
        """Folders with good coverage but no dominant date go to _mixed_dates."""
        from photosort.planner.analyzer import FolderDateAnalysis
        from photosort.planner.resolver import resolve_folder, PlannerConfig

        # Good coverage, within span, but only 50% agreement
        analysis = FolderDateAnalysis(
            total_files=10,
            image_files=10,
            images_with_date=10,
            date_coverage_pct=1.0,
            prevalent_date=20231015,
            prevalent_date_count=5,
            prevalent_date_pct=0.50,  # Below 80% threshold
            min_date=20231010,
            max_date=20231020,
            date_span_months=0,
            unique_date_count=5,
        )

        config = PlannerConfig()
        result = resolve_folder(analysis, config)

        assert result.bucket == "mixed_dates"
        assert result.source == "no_consensus"

    def test_path_date_overrides_analysis(self, temp_db: Database) -> None:
        """If folder has path-derived date, it overrides statistical analysis."""
        from photosort.planner.resolver import resolve_folder_with_path_date

        # Folder path contains date: "2023/2023_10/20231015-sunset"
        path_date = 20231015

        result = resolve_folder_with_path_date(path_date)

        assert result.bucket is None
        assert result.resolved_date == 20231015
        assert result.source == "path_date"

    def test_edge_case_exactly_at_threshold(self, temp_db: Database) -> None:
        """Test behavior at exact threshold boundaries."""
        from photosort.planner.analyzer import FolderDateAnalysis
        from photosort.planner.resolver import resolve_folder, PlannerConfig

        # Exactly 30% coverage (at threshold)
        analysis = FolderDateAnalysis(
            total_files=10,
            image_files=10,
            images_with_date=3,
            date_coverage_pct=0.30,  # Exactly at threshold
            prevalent_date=20231015,
            prevalent_date_count=3,
            prevalent_date_pct=1.0,
            min_date=20231015,
            max_date=20231015,
            date_span_months=0,
            unique_date_count=1,
        )

        config = PlannerConfig()
        result = resolve_folder(analysis, config)

        # At threshold means it passes (>= not >)
        assert result.bucket is None
        assert result.resolved_date == 20231015


# =============================================================================
# Tests: Target Path Construction
# =============================================================================


class TestTargetPathConstruction:
    """Tests for building target paths from resolved dates."""

    def test_basic_target_path(self) -> None:
        """Basic target path structure: yyyy/yyyy_mm/yyyymmdd/"""
        from photosort.planner.path_builder import build_target_folder

        result = build_target_folder(resolved_date=20231015, annotation=None)

        assert result == "2023/2023_10/20231015"

    def test_target_path_with_annotation(self) -> None:
        """Target path with annotation appended."""
        from photosort.planner.path_builder import build_target_folder

        result = build_target_folder(resolved_date=20231015, annotation="sunset")

        assert result == "2023/2023_10/20231015-sunset"

    def test_annotation_extraction_strips_date_prefix(self) -> None:
        """Extract annotation by stripping date prefix from folder name."""
        from photosort.planner.path_builder import extract_annotation

        test_cases = [
            # (folder_name, resolved_date, expected_annotation)
            ("20231015-sunset", 20231015, "sunset"),
            ("20231015_sunset", 20231015, "sunset"),
            ("2023-10-15-sunset", 20231015, "sunset"),
            ("2023_10_15_sunset", 20231015, "sunset"),
            ("sunset", 20231015, "sunset"),  # No date prefix
            ("20231015", 20231015, None),  # Just the date, no annotation
        ]

        for folder_name, resolved_date, expected in test_cases:
            result = extract_annotation(folder_name, resolved_date)
            assert result == expected, f"Failed for {folder_name}"

    def test_annotation_truncation(self) -> None:
        """Annotations longer than 10 chars are truncated."""
        from photosort.planner.path_builder import extract_annotation

        result = extract_annotation("20231015-this_is_a_very_long_annotation", 20231015)

        assert result is not None
        assert len(result) <= 10

    def test_annotation_not_stripped_if_date_mismatch(self) -> None:
        """Don't strip date prefix if it doesn't match resolved date."""
        from photosort.planner.path_builder import extract_annotation

        # Folder has 20231015 in name, but resolved date is different
        result = extract_annotation("20231015-sunset", 20230901)

        # Should keep the full name as annotation (different date)
        assert result is not None
        assert "20231015" in result or result == "20231015-sun"  # Truncated

    def test_bucket_path_construction(self) -> None:
        """Bucket paths preserve original folder structure."""
        from photosort.planner.path_builder import build_bucket_path

        result = build_bucket_path(
            bucket="mixed_dates",
            source_folder="Photos/2023/vacation",
        )

        assert result == "_mixed_dates/Photos/2023/vacation"


# =============================================================================
# Tests: Duplicate Filename Handling
# =============================================================================


class TestDuplicateHandling:
    """Tests for handling duplicate filenames in target folders."""

    def test_no_duplicate_no_change(self) -> None:
        """When no duplicate exists, filename unchanged."""
        from photosort.planner.path_builder import resolve_filename_duplicate

        existing = {"other_photo.jpg", "another.arw"}
        result = resolve_filename_duplicate("photo.jpg", "some/source/path", existing)

        assert result.filename == "photo.jpg"
        assert result.is_duplicate is False

    def test_duplicate_gets_hash_suffix(self) -> None:
        """When duplicate exists, filename gets hash suffix."""
        from photosort.planner.path_builder import resolve_filename_duplicate

        existing = {"photo.jpg"}  # Already exists
        result = resolve_filename_duplicate("photo.jpg", "some/source/path", existing)

        assert result.is_duplicate is True
        assert result.filename != "photo.jpg"
        assert result.filename.startswith("photo_dupe_")
        assert result.filename.endswith(".jpg")

    def test_duplicate_hash_is_deterministic(self) -> None:
        """Same source path produces same hash."""
        from photosort.planner.path_builder import resolve_filename_duplicate

        existing = {"photo.jpg"}
        result1 = resolve_filename_duplicate("photo.jpg", "path/a", existing)
        result2 = resolve_filename_duplicate("photo.jpg", "path/a", existing)

        assert result1.filename == result2.filename

    def test_different_sources_get_different_hashes(self) -> None:
        """Different source paths produce different hashes."""
        from photosort.planner.path_builder import resolve_filename_duplicate

        existing = {"photo.jpg"}
        result1 = resolve_filename_duplicate("photo.jpg", "path/a", existing)
        result2 = resolve_filename_duplicate("photo.jpg", "path/b", existing)

        assert result1.filename != result2.filename


# =============================================================================
# Tests: Sidecar Detection
# =============================================================================


class TestSidecarDetection:
    """Tests for identifying sidecar files."""

    def test_xmp_is_sidecar(self) -> None:
        """XMP files are sidecars if matching image exists."""
        from photosort.planner.sidecar import detect_sidecar

        folder_files = [
            {"filename_base": "IMG_1234", "extension": "arw"},
            {"filename_base": "IMG_1234", "extension": "xmp"},
        ]

        result = detect_sidecar(
            filename_base="IMG_1234",
            extension="xmp",
            folder_files=folder_files,
        )

        assert result is True

    def test_xmp_not_sidecar_if_no_match(self) -> None:
        """XMP files are not sidecars if no matching image."""
        from photosort.planner.sidecar import detect_sidecar

        folder_files = [
            {"filename_base": "OTHER_FILE", "extension": "arw"},
            {"filename_base": "IMG_1234", "extension": "xmp"},
        ]

        result = detect_sidecar(
            filename_base="IMG_1234",
            extension="xmp",
            folder_files=folder_files,
        )

        assert result is False

    def test_thm_is_sidecar(self) -> None:
        """THM (thumbnail) files are sidecars."""
        from photosort.planner.sidecar import detect_sidecar

        folder_files = [
            {"filename_base": "MVI_1234", "extension": "mov"},
            {"filename_base": "MVI_1234", "extension": "thm"},
        ]

        result = detect_sidecar(
            filename_base="MVI_1234",
            extension="thm",
            folder_files=folder_files,
        )

        assert result is True

    def test_regular_image_not_sidecar(self) -> None:
        """Regular images are not sidecars."""
        from photosort.planner.sidecar import detect_sidecar

        folder_files = [
            {"filename_base": "IMG_1234", "extension": "jpg"},
            {"filename_base": "IMG_1234", "extension": "arw"},
        ]

        result = detect_sidecar(
            filename_base="IMG_1234",
            extension="jpg",
            folder_files=folder_files,
        )

        assert result is False


# =============================================================================
# Tests: Image Extension Classification
# =============================================================================


class TestImageExtensionClassification:
    """Tests for is_image_extension() helper."""

    def test_raw_formats_are_images(self) -> None:
        """RAW formats are classified as images."""
        from photosort.planner.analyzer import is_image_extension

        raw_extensions = ["arw", "nef", "cr2", "dng", "orf", "raf", "srw"]
        for ext in raw_extensions:
            assert is_image_extension(ext) is True, f"{ext} should be image"

    def test_common_formats_are_images(self) -> None:
        """Common image formats are classified as images."""
        from photosort.planner.analyzer import is_image_extension

        common_extensions = ["jpg", "jpeg", "png", "tif", "tiff", "heic", "gif", "bmp"]
        for ext in common_extensions:
            assert is_image_extension(ext) is True, f"{ext} should be image"

    def test_video_formats_are_not_images(self) -> None:
        """Video formats are NOT classified as images for folder analysis."""
        from photosort.planner.analyzer import is_image_extension

        video_extensions = ["mp4", "mov", "avi", "mkv", "m4v"]
        for ext in video_extensions:
            assert is_image_extension(ext) is False, f"{ext} should not be image"

    def test_document_formats_are_not_images(self) -> None:
        """Document formats are not images."""
        from photosort.planner.analyzer import is_image_extension

        doc_extensions = ["pdf", "doc", "txt", "xls"]
        for ext in doc_extensions:
            assert is_image_extension(ext) is False, f"{ext} should not be image"


# =============================================================================
# Tests: Round-Trip Integration
# =============================================================================


class TestRoundTripIntegration:
    """Integration tests showing complete planning pipeline.

    These tests insert files + metadata into the database and verify
    the complete planning output, making input→output relationships clear.
    """

    def test_single_folder_all_same_date(self, temp_db: Database) -> None:
        """
        Scenario: Single folder with 3 images, all from same date.
        Expected: Folder resolves to that date.

        Input:
            folder: "vacation/"
            files:  IMG_001.jpg (EXIF: 20231015)
                    IMG_002.jpg (EXIF: 20231015)
                    IMG_003.arw (EXIF: 20231015)

        Expected Output:
            folder_plan: resolved_date=20231015, source="prevalent_date"
            file_plan:   target_folder="2023/2023_10/20231015"
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        # Insert files
        files = [
            FileData(source_path="vacation/IMG_001.jpg", extension="jpg"),
            FileData(source_path="vacation/IMG_002.jpg", extension="jpg"),
            FileData(source_path="vacation/IMG_003.arw", extension="arw"),
        ]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)

        # Insert metadata (all same EXIF date)
        for file_id in file_ids:
            insert_metadata(temp_db, MetadataData(file_id=file_id, date_original=20231015))

        # Run planner
        planner = Planner(temp_db)
        planner.plan(session_id)

        # Verify folder_plan
        cursor = temp_db.conn.execute(
            "SELECT resolved_date, resolved_date_source, bucket FROM folder_plan WHERE source_folder = ?",
            ("vacation",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["resolved_date"] == 20231015
        assert row["bucket"] is None

        # Verify file_plan - folder name "vacation" becomes annotation
        cursor = temp_db.conn.execute(
            """
            SELECT target_folder FROM file_plan
            WHERE folder_plan_id = (SELECT id FROM folder_plan WHERE source_folder = ?)
            """,
            ("vacation",),
        )
        rows = cursor.fetchall()
        assert len(rows) == 3
        assert all(row["target_folder"] == "2023/2023_10/20231015-vacation" for row in rows)

    def test_folder_with_path_date_overrides_exif(self, temp_db: Database) -> None:
        """
        Scenario: Folder has path-based date that differs from EXIF dates.
        Expected: Path date takes precedence.

        Input:
            folder: "2023/2023_10/20231020-birthday/"
            files:  photo.jpg (path_folder=20231020, EXIF=20230501)

        Expected Output:
            resolved_date=20231020 (from path, not EXIF)
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [
            FileData(
                source_path="2023/2023_10/20231020-birthday/photo.jpg",
                extension="jpg",
                date_path_folder=20231020,  # Path date
            ),
        ]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)

        # Insert metadata with different EXIF date
        insert_metadata(temp_db, MetadataData(file_id=file_ids[0], date_original=20230501))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT resolved_date, resolved_date_source FROM folder_plan WHERE source_folder = ?",
            ("2023/2023_10/20231020-birthday",),
        )
        row = cursor.fetchone()
        assert row["resolved_date"] == 20231020
        assert row["resolved_date_source"] == "path_date"

    def test_folder_low_coverage_goes_to_bucket(self, temp_db: Database) -> None:
        """
        Scenario: Folder with many images but few have dates.
        Expected: Goes to _mixed_dates bucket.

        Input:
            folder: "old_photos/"
            files:  10 JPGs, only 2 have EXIF dates (20% coverage)

        Expected Output:
            bucket="mixed_dates", source="low_coverage"
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        # 10 images
        files = [
            FileData(source_path=f"old_photos/IMG_{i:04d}.jpg", extension="jpg") for i in range(10)
        ]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)

        # Only 2 have metadata
        insert_metadata(temp_db, MetadataData(file_id=file_ids[0], date_original=20231015))
        insert_metadata(temp_db, MetadataData(file_id=file_ids[1], date_original=20231015))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT bucket, resolved_date_source FROM folder_plan WHERE source_folder = ?",
            ("old_photos",),
        )
        row = cursor.fetchone()
        assert row["bucket"] == "mixed_dates"
        assert row["resolved_date_source"] == "low_coverage"

    def test_folder_wide_date_spread_goes_to_bucket(self, temp_db: Database) -> None:
        """
        Scenario: Folder with images spanning many months.
        Expected: Goes to _mixed_dates bucket.

        Input:
            folder: "misc/"
            files:  5 JPGs with dates spanning Jan to Jun 2023 (6 months)

        Expected Output:
            bucket="mixed_dates", source="wide_spread"
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [FileData(source_path=f"misc/IMG_{i}.jpg", extension="jpg") for i in range(5)]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)

        # Dates spanning 6 months
        dates = [20230115, 20230215, 20230315, 20230415, 20230615]
        for file_id, date in zip(file_ids, dates, strict=True):
            insert_metadata(temp_db, MetadataData(file_id=file_id, date_original=date))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT bucket, resolved_date_source, date_span_months FROM folder_plan WHERE source_folder = ?",
            ("misc",),
        )
        row = cursor.fetchone()
        assert row["bucket"] == "mixed_dates"
        assert row["resolved_date_source"] == "wide_spread"

    def test_folder_no_images_goes_to_non_media(self, temp_db: Database) -> None:
        """
        Scenario: Folder with only non-image files.
        Expected: Goes to _non_media bucket.

        Input:
            folder: "documents/"
            files:  report.pdf, notes.txt, data.csv

        Expected Output:
            bucket="non_media", source="no_images"
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [
            FileData(source_path="documents/report.pdf", extension="pdf"),
            FileData(source_path="documents/notes.txt", extension="txt"),
            FileData(source_path="documents/data.csv", extension="csv"),
        ]
        insert_files_and_metadata(temp_db, session_id, files, None)

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT bucket, resolved_date_source FROM folder_plan WHERE source_folder = ?",
            ("documents",),
        )
        row = cursor.fetchone()
        assert row["bucket"] == "non_media"
        assert row["resolved_date_source"] == "no_images"

    def test_annotation_extracted_from_folder_name(self, temp_db: Database) -> None:
        """
        Scenario: Folder with date and annotation in name.
        Expected: Annotation preserved in target path.

        Input:
            folder: "20231015-sunset/"
            files:  photo.jpg (EXIF: 20231015)

        Expected Output:
            target_folder="2023/2023_10/20231015-sunset"
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [
            FileData(
                source_path="20231015-sunset/photo.jpg",
                extension="jpg",
                date_path_folder=20231015,
            ),
        ]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)
        insert_metadata(temp_db, MetadataData(file_id=file_ids[0], date_original=20231015))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT target_folder, annotation FROM folder_plan WHERE source_folder = ?",
            ("20231015-sunset",),
        )
        row = cursor.fetchone()
        assert row["target_folder"] == "2023/2023_10/20231015-sunset"
        assert row["annotation"] == "sunset"

    def test_file_fallback_to_fs_modified(self, temp_db: Database) -> None:
        """
        Scenario: File with no path date and no EXIF, only fs_modified.
        Expected: Uses fs_modified as date source.

        Input:
            folder: "random/"
            files:  file.jpg (no path date, no EXIF, fs_modified=2021-06-15)

        Expected Output:
            file's resolved_date=20210615, source="fs_modified"
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        # Unix timestamp for 2021-06-15
        fs_modified = 1623715200.0

        files = [
            FileData(
                source_path="random/file.jpg",
                extension="jpg",
                fs_modified_at_unix=fs_modified,
            ),
        ]
        insert_files_and_metadata(temp_db, session_id, files, None)

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            """
            SELECT file_resolved_date, file_date_source
            FROM file_plan fp JOIN files f ON fp.file_id = f.id
            WHERE f.source_path = ?
            """,
            ("random/file.jpg",),
        )
        row = cursor.fetchone()
        assert row["file_resolved_date"] == 20210615
        assert row["file_date_source"] == "fs_modified"

    def test_duplicate_filename_handling(self, temp_db: Database) -> None:
        """
        Scenario: Two files from different folders with path dates would have same target.
        Expected: Second file gets hash suffix.

        Input:
            folder1: "2023/2023_10/20231015/trip1/photo.jpg" (path_date: 20231015)
            folder2: "2023/2023_10/20231015/trip2/photo.jpg" (path_date: 20231015)

        Both have the same path date and go to the same target folder
        (since path date takes priority and both folders have it).

        Expected Output:
            Both go to same target folder: 2023/2023_10/20231015-trip1 and 20231015-trip2
            Wait - they have different folder names so different targets.

        Let's use same folder name to actually trigger duplicate:
            folder1: "20231015-shoot/photo.jpg" (path_date: 20231015)
            folder2: "20231015-shoot/photo.jpg" from different session - can't do that

        Actually, let's simulate two files in same source folder with same name - that's
        not possible in filesystem. The real scenario is files from different source folders
        that merge into the same target folder. For that, both source folders need to
        resolve to the same target.

        Correct test: Two source folders that resolve to SAME target folder.
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        # Both source folders have the same path-based date prefix
        # Both will resolve to 2023/2023_10/20231015-shoot (same annotation)
        files = [
            FileData(
                source_path="20231015-shoot/photo.jpg",
                extension="jpg",
                date_path_folder=20231015,
            ),
            FileData(
                source_path="elsewhere/20231015-shoot/photo.jpg",
                extension="jpg",
                date_path_folder=20231015,
            ),
        ]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)

        for file_id in file_ids:
            insert_metadata(temp_db, MetadataData(file_id=file_id, date_original=20231015))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT target_filename, is_potential_duplicate, target_folder FROM file_plan"
        )
        rows = cursor.fetchall()

        # Check what target folders we got
        target_folders = set(row["target_folder"] for row in rows)
        filenames = [row["target_filename"] for row in rows]
        duplicates = [row["is_potential_duplicate"] for row in rows]

        # Both should go to same target folder (same path date + same annotation)
        assert len(target_folders) == 1, f"Expected 1 target folder, got {target_folders}"

        # One should be original, one should be duplicate
        assert "photo.jpg" in filenames
        assert any("_dupe_" in f for f in filenames)
        assert sum(duplicates) == 1

    def test_multiple_folders_different_dates(self, temp_db: Database) -> None:
        """
        Scenario: Multiple folders, each with different dates.
        Expected: Each folder resolves independently.

        Input:
            folder1: "trip_oct/" with EXIF dates 20231015
            folder2: "trip_nov/" with EXIF dates 20231115

        Expected Output:
            trip_oct → 2023/2023_10/20231015-trip_oct (folder name as annotation)
            trip_nov → 2023/2023_11/20231115-trip_nov
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [
            FileData(source_path="trip_oct/IMG_001.jpg", extension="jpg"),
            FileData(source_path="trip_oct/IMG_002.jpg", extension="jpg"),
            FileData(source_path="trip_nov/IMG_001.jpg", extension="jpg"),
            FileData(source_path="trip_nov/IMG_002.jpg", extension="jpg"),
        ]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)

        # Oct files
        insert_metadata(temp_db, MetadataData(file_id=file_ids[0], date_original=20231015))
        insert_metadata(temp_db, MetadataData(file_id=file_ids[1], date_original=20231015))
        # Nov files
        insert_metadata(temp_db, MetadataData(file_id=file_ids[2], date_original=20231115))
        insert_metadata(temp_db, MetadataData(file_id=file_ids[3], date_original=20231115))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT source_folder, target_folder FROM folder_plan ORDER BY source_folder"
        )
        rows = {row["source_folder"]: row["target_folder"] for row in cursor.fetchall()}

        # Folder names become annotations (truncated to 10 chars)
        assert rows["trip_nov"] == "2023/2023_11/20231115-trip_nov"
        assert rows["trip_oct"] == "2023/2023_10/20231015-trip_oct"

    def test_sidecar_detection_in_plan(self, temp_db: Database) -> None:
        """
        Scenario: RAW file with matching XMP sidecar.
        Expected: XMP marked as sidecar in file_plan.

        Input:
            folder: "shoot/"
            files:  IMG_001.arw, IMG_001.xmp

        Expected Output:
            IMG_001.xmp has is_sidecar=True
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [
            FileData(source_path="shoot/IMG_001.arw", extension="arw"),
            FileData(source_path="shoot/IMG_001.xmp", extension="xmp"),
        ]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)
        insert_metadata(temp_db, MetadataData(file_id=file_ids[0], date_original=20231015))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            """
            SELECT fp.is_sidecar, f.extension
            FROM file_plan fp
            JOIN files f ON fp.file_id = f.id
            WHERE f.directory_path = 'shoot'
            """
        )
        rows = {row["extension"]: row["is_sidecar"] for row in cursor.fetchall()}

        assert rows.get("arw") is False or rows.get("arw") == 0
        assert rows.get("xmp") is True or rows.get("xmp") == 1


# =============================================================================
# Tests: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_folder(self, temp_db: Database) -> None:
        """Folders with no files should not appear in plan."""
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)
        # Don't insert any files

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute("SELECT COUNT(*) as cnt FROM folder_plan")
        assert cursor.fetchone()["cnt"] == 0

    def test_file_with_no_extension(self, temp_db: Database) -> None:
        """Files without extension should be handled gracefully."""
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [
            FileData(source_path="misc/README", extension=None),
        ]
        insert_files_and_metadata(temp_db, session_id, files, None)

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute("SELECT bucket FROM folder_plan")
        row = cursor.fetchone()
        assert row["bucket"] == "non_media"  # No extension = not an image

    def test_nested_date_hierarchy_uses_deepest(self, temp_db: Database) -> None:
        """
        Nested date hierarchies should use the deepest (most specific) date.

        Input:
            folder: "2023/2023_10/20231015/2024/2024_01/subfolder/"
            file with path_folder date from the 2024 hierarchy

        Expected: Uses 2024 date, not 2023
        """
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [
            FileData(
                source_path="2023/2023_10/20231015/2024/2024_01/subfolder/photo.jpg",
                extension="jpg",
                date_path_folder=20240115,  # From deeper 2024 hierarchy
            ),
        ]
        insert_files_and_metadata(temp_db, session_id, files, None)

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT resolved_date FROM folder_plan WHERE source_folder = ?",
            ("2023/2023_10/20231015/2024/2024_01/subfolder",),
        )
        row = cursor.fetchone()
        assert row["resolved_date"] == 20240115

    def test_replanning_clears_old_data(self, temp_db: Database) -> None:
        """Re-running planner should clear old plan data."""
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [FileData(source_path="folder/photo.jpg", extension="jpg")]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)
        insert_metadata(temp_db, MetadataData(file_id=file_ids[0], date_original=20231015))

        planner = Planner(temp_db)

        # Plan once
        planner.plan(session_id)

        cursor = temp_db.conn.execute("SELECT COUNT(*) as cnt FROM folder_plan")
        first_count = cursor.fetchone()["cnt"]

        # Plan again
        planner.plan(session_id)

        cursor = temp_db.conn.execute("SELECT COUNT(*) as cnt FROM folder_plan")
        second_count = cursor.fetchone()["cnt"]

        # Should have same count (not doubled)
        assert first_count == second_count

    def test_very_old_date(self, temp_db: Database) -> None:
        """Handle files with very old dates (e.g., 1990s)."""
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [FileData(source_path="old/photo.jpg", extension="jpg")]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)
        insert_metadata(temp_db, MetadataData(file_id=file_ids[0], date_original=19950715))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT target_folder FROM folder_plan WHERE source_folder = 'old'"
        )
        row = cursor.fetchone()
        # Folder name "old" becomes annotation since it has no date prefix
        assert row["target_folder"] == "1995/1995_07/19950715-old"

    def test_future_date(self, temp_db: Database) -> None:
        """Handle files with future dates (possibly incorrect metadata)."""
        from photosort.planner.planner import Planner

        session_id = insert_scan_session(temp_db)

        files = [FileData(source_path="future/photo.jpg", extension="jpg")]
        file_ids = insert_files_and_metadata(temp_db, session_id, files, None)
        insert_metadata(temp_db, MetadataData(file_id=file_ids[0], date_original=20501231))

        planner = Planner(temp_db)
        planner.plan(session_id)

        cursor = temp_db.conn.execute(
            "SELECT target_folder FROM folder_plan WHERE source_folder = 'future'"
        )
        row = cursor.fetchone()
        # Folder name "future" becomes annotation since it has no date prefix
        # Should still work, even with future date
        assert row["target_folder"] == "2050/2050_12/20501231-future"
