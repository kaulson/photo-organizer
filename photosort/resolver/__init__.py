"""Date extraction strategies for path-based date resolution."""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath


@dataclass
class DateExtraction:
    """Result of a date extraction attempt."""

    date_int: int | None  # YYYYMMDD as integer
    source: str | None  # The matched segment for debugging


# Regex patterns for date components
YEAR_PATTERN = r"(19\d{2}|20\d{2})"
MONTH_PATTERN = r"(0[1-9]|1[0-2])"
DAY_PATTERN = r"(0[1-9]|[12]\d|3[01])"

# Pattern for folder/filename date: YYYYMMDD or YYYY-MM-DD or YYYY_MM_DD
# Must be bounded by start/end or non-digit
DATE_PATTERN = re.compile(
    rf"(?:^|[^0-9]){YEAR_PATTERN}[-_]?{MONTH_PATTERN}[-_]?{DAY_PATTERN}(?:[^0-9]|$)"
)


def is_valid_date(year: int, month: int, day: int) -> bool:
    """Check if the given year, month, day form a valid date."""
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def to_date_int(year: int, month: int, day: int) -> int:
    """Convert year, month, day to YYYYMMDD integer."""
    return year * 10000 + month * 100 + day


def extract_hierarchy_date(path: str) -> DateExtraction:
    """
    Extract date from yyyy/mm/dd folder hierarchy.

    Looks for three consecutive folders forming a valid date.
    If multiple valid hierarchies exist, returns the deepest one.
    """
    parts = PurePosixPath(path).parts

    # Need at least 4 parts: yyyy/mm/dd/filename
    if len(parts) < 4:
        return DateExtraction(None, None)

    best_match: DateExtraction = DateExtraction(None, None)

    # Check consecutive triples, starting from deeper paths (reverse order)
    for i in range(len(parts) - 4, -1, -1):
        year_str, month_str, day_str = parts[i], parts[i + 1], parts[i + 2]

        if not (
            _is_year_folder(year_str) and _is_month_folder(month_str) and _is_day_folder(day_str)
        ):
            continue

        year = int(year_str)
        month = int(month_str)
        day = int(day_str)

        if is_valid_date(year, month, day):
            source = f"{year_str}/{month_str}/{day_str}"
            best_match = DateExtraction(to_date_int(year, month, day), source)
            break  # Found deepest match

    return best_match


def extract_folder_date(path: str) -> DateExtraction:
    """
    Extract date from a single folder containing a date pattern.

    Checks each folder in the path for date patterns like:
    - 20230514
    - 2023_05_14
    - 2023-05-14
    - 20230514-sunset
    - sunset-20230514

    If multiple folders contain dates, returns the deepest one.
    """
    parts = PurePosixPath(path).parts

    # Skip filename (last part)
    if len(parts) < 2:
        return DateExtraction(None, None)

    folder_parts = parts[:-1]

    # Check folders from deepest to shallowest
    for folder in reversed(folder_parts):
        result = _extract_date_from_string(folder)
        if result.date_int is not None:
            return DateExtraction(result.date_int, folder)

    return DateExtraction(None, None)


def extract_filename_date(filename: str) -> DateExtraction:
    """
    Extract date from filename.

    Looks for date patterns like:
    - IMG_20230514_143052.jpg
    - 20230514_IMG_001.arw
    - photo_2023-05-14.jpg

    If multiple dates exist, returns the leftmost one.
    """
    result = _extract_date_from_string(filename)
    if result.date_int is not None:
        return DateExtraction(result.date_int, filename)
    return DateExtraction(None, None)


def _extract_date_from_string(text: str) -> DateExtraction:
    """Extract the first valid date from a string."""
    match = DATE_PATTERN.search(text)
    if not match:
        return DateExtraction(None, None)

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))

    if is_valid_date(year, month, day):
        return DateExtraction(to_date_int(year, month, day), text)

    return DateExtraction(None, None)


def _is_year_folder(name: str) -> bool:
    """Check if folder name is exactly a 4-digit year (1900-2099)."""
    if len(name) != 4 or not name.isdigit():
        return False
    year = int(name)
    return 1900 <= year <= 2099


def _is_month_folder(name: str) -> bool:
    """Check if folder name is exactly a 2-digit month (01-12)."""
    if len(name) != 2 or not name.isdigit():
        return False
    month = int(name)
    return 1 <= month <= 12


def _is_day_folder(name: str) -> bool:
    """Check if folder name is exactly a 2-digit day (01-31)."""
    if len(name) != 2 or not name.isdigit():
        return False
    day = int(name)
    return 1 <= day <= 31
