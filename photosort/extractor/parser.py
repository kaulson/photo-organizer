"""Metadata field parsing utilities."""

import json
import re
from datetime import datetime
from typing import Any


EXCLUDED_FIELDS = {
    "EXIF:ThumbnailImage",
    "EXIF:ThumbnailTIFF",
    "EXIF:PreviewImage",
    "EXIF:JpgFromRaw",
    "EXIF:OtherImage",
    "ICC_Profile:ProfileCMMType",
    "File:Directory",
    "File:FileName",
    "SourceFile",
}


def parse_exif_date(date_str: str | None) -> tuple[float | None, int | None]:
    """Parse EXIF date string to (unix_timestamp, YYYYMMDD)."""
    if not date_str or not isinstance(date_str, str):
        return None, None

    date_str = date_str.strip()
    if not date_str or date_str == "0000:00:00 00:00:00":
        return None, None

    formats = [
        "%Y:%m:%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ]

    tz_pattern = re.compile(r"([+-]\d{2}:\d{2})$")
    date_str_clean = tz_pattern.sub("", date_str)

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str_clean, fmt.replace("%z", ""))
            unix_ts = dt.timestamp()
            date_int = dt.year * 10000 + dt.month * 100 + dt.day
            return unix_ts, date_int
        except ValueError:
            continue

    return None, None


def get_first_value(metadata: dict, *keys: str) -> Any:
    """Get first non-None value from metadata by keys."""
    for key in keys:
        if key in metadata and metadata[key] is not None:
            return metadata[key]
    return None


def extract_metadata_families(metadata: dict) -> str:
    """Extract unique group names from metadata keys."""
    families: set[str] = set()
    for key in metadata:
        if ":" in key:
            families.add(key.split(":")[0])
    return ",".join(sorted(families))


def filter_metadata_for_json(metadata: dict) -> dict:
    """Filter metadata for JSON storage, removing binary data."""
    filtered = {}
    for key, value in metadata.items():
        if key in EXCLUDED_FIELDS:
            continue
        if isinstance(value, str):
            if value.startswith("base64:") or value.startswith("(Binary data"):
                continue
        filtered[key] = value
    return filtered


def metadata_to_json(metadata: dict) -> str:
    """Convert filtered metadata to JSON string."""
    filtered = filter_metadata_for_json(metadata)
    return json.dumps(filtered, ensure_ascii=False, separators=(",", ":"))
