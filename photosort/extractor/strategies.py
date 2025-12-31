"""Extraction strategies for MetadataExtractor."""

import sqlite3
from typing import Protocol


SUPPORTED_EXTENSIONS = {
    "arw",
    "jpg",
    "jpeg",
    "nef",
    "dng",
    "tif",
    "tiff",
    "heic",
    "cr2",
    "srw",
    "mp4",
    "m4v",
    "mov",
    "mkv",
    "avi",
}


class ExtractionStrategy(Protocol):
    """Protocol for metadata extraction strategies."""

    name: str

    def get_file_ids(self, conn: sqlite3.Connection, limit: int | None = None) -> list[int]:
        """Return list of file IDs to process."""


class FullStrategy:
    """Process all files with supported extensions."""

    name = "full"

    def get_file_ids(self, conn: sqlite3.Connection, limit: int | None = None) -> list[int]:
        extensions_placeholders = ",".join("?" for _ in SUPPORTED_EXTENSIONS)
        extensions_list = list(SUPPORTED_EXTENSIONS)

        query = f"""
            SELECT f.id FROM files f
            WHERE f.extension IN ({extensions_placeholders})
              AND f.id NOT IN (SELECT file_id FROM file_metadata)
            ORDER BY f.id
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor = conn.execute(query, extensions_list)
        return [row[0] for row in cursor.fetchall()]


class SelectiveStrategy:
    """Process only files without path-based dates."""

    name = "selective"

    def get_file_ids(self, conn: sqlite3.Connection, limit: int | None = None) -> list[int]:
        extensions_placeholders = ",".join("?" for _ in SUPPORTED_EXTENSIONS)
        extensions_list = list(SUPPORTED_EXTENSIONS)

        query = f"""
            SELECT f.id FROM files f
            WHERE f.extension IN ({extensions_placeholders})
              AND f.date_path_folder IS NULL
              AND f.date_path_filename IS NULL
              AND f.id NOT IN (SELECT file_id FROM file_metadata)
            ORDER BY f.id
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor = conn.execute(query, extensions_list)
        return [row[0] for row in cursor.fetchall()]


def get_strategy(name: str) -> ExtractionStrategy:
    """Get extraction strategy by name."""
    strategies: dict[str, ExtractionStrategy] = {
        "full": FullStrategy(),
        "selective": SelectiveStrategy(),
    }
    if name not in strategies:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(strategies.keys())}")
    return strategies[name]
