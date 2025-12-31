"""File and folder date resolution logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from photosort.planner.analyzer import FolderDateAnalysis


@dataclass
class PlannerConfig:
    """Configuration for planner thresholds."""

    # Minimum percentage of images that must have dates
    # Below this, folder goes to _mixed_dates
    min_coverage_threshold: float = 0.30  # 30%

    # Minimum percentage agreement on prevalent date
    # Above this, use the prevalent date
    min_prevalence_threshold: float = 0.80  # 80%

    # Maximum date spread in calendar months
    # At or above this, folder goes to _mixed_dates
    max_date_span_months: int = 3


@dataclass
class FileDateResult:
    """Result of resolving a file's date."""

    date: int | None  # YYYYMMDD or None
    source: str  # 'path_folder', 'path_filename', 'exif', 'fs_modified', 'none'


@dataclass
class FolderResolution:
    """Result of resolving a folder's target location."""

    bucket: str | None  # None, 'mixed_dates', or 'non_media'
    resolved_date: int | None  # YYYYMMDD or None if bucketed
    source: str  # Resolution source for debugging


def resolve_file_date(
    *,
    date_path_folder: int | None,
    date_path_filename: int | None,
    date_exif: int | None,
    fs_modified_unix: float | None,
) -> FileDateResult:
    """Resolve a file's date using priority hierarchy.

    Priority order:
    1. Path folder date (highest)
    2. Path filename date
    3. EXIF date
    4. Filesystem modified date (lowest)

    Args:
        date_path_folder: Date from folder path pattern (YYYYMMDD).
        date_path_filename: Date from filename pattern (YYYYMMDD).
        date_exif: Date from EXIF metadata (YYYYMMDD).
        fs_modified_unix: Filesystem modified timestamp (Unix epoch).

    Returns:
        FileDateResult with resolved date and source.
    """
    # 1. Path folder date (highest priority)
    if date_path_folder is not None:
        return FileDateResult(date=date_path_folder, source="path_folder")

    # 2. Path filename date
    if date_path_filename is not None:
        return FileDateResult(date=date_path_filename, source="path_filename")

    # 3. EXIF date
    if date_exif is not None:
        return FileDateResult(date=date_exif, source="exif")

    # 4. Filesystem modified date (fallback)
    if fs_modified_unix is not None:
        date = _unix_to_yyyymmdd(fs_modified_unix)
        return FileDateResult(date=date, source="fs_modified")

    # No date available
    return FileDateResult(date=None, source="none")


def _unix_to_yyyymmdd(unix_timestamp: float) -> int:
    """Convert Unix timestamp to YYYYMMDD integer.

    Args:
        unix_timestamp: Seconds since Unix epoch.

    Returns:
        Date as YYYYMMDD integer.
    """
    dt = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
    return dt.year * 10000 + dt.month * 100 + dt.day


def resolve_folder(
    analysis: FolderDateAnalysis,
    config: PlannerConfig,
) -> FolderResolution:
    """Apply resolution rules to folder analysis.

    Args:
        analysis: FolderDateAnalysis with folder statistics.
        config: PlannerConfig with thresholds.

    Returns:
        FolderResolution indicating where folder files should go.
    """
    # No images at all → non_media bucket
    if analysis.image_files == 0:
        return FolderResolution(
            bucket="non_media",
            resolved_date=None,
            source="no_images",
        )

    # Low date coverage → mixed_dates bucket
    if analysis.date_coverage_pct < config.min_coverage_threshold:
        return FolderResolution(
            bucket="mixed_dates",
            resolved_date=None,
            source="low_coverage",
        )

    # Wide date spread → mixed_dates bucket
    if analysis.date_span_months >= config.max_date_span_months:
        return FolderResolution(
            bucket="mixed_dates",
            resolved_date=None,
            source="wide_spread",
        )

    # High prevalence → use prevalent date
    if analysis.prevalent_date_pct >= config.min_prevalence_threshold:
        return FolderResolution(
            bucket=None,
            resolved_date=analysis.prevalent_date,
            source="prevalent_date",
        )

    # 100% agreement (all dated images have same date)
    if analysis.unique_date_count == 1 and analysis.prevalent_date is not None:
        return FolderResolution(
            bucket=None,
            resolved_date=analysis.prevalent_date,
            source="unanimous",
        )

    # Fallback: mixed_dates
    return FolderResolution(
        bucket="mixed_dates",
        resolved_date=None,
        source="no_consensus",
    )


def resolve_folder_with_path_date(
    path_date: int,
) -> FolderResolution:
    """Resolve a folder that has a path-derived date.

    When a folder has a date in its path, that date takes precedence
    over any statistical analysis.

    Args:
        path_date: Date extracted from path (YYYYMMDD).

    Returns:
        FolderResolution with the path date.
    """
    return FolderResolution(
        bucket=None,
        resolved_date=path_date,
        source="path_date",
    )
