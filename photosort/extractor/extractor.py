"""MetadataExtractor implementation."""

import logging
import time
from dataclasses import dataclass, field

from photosort.database.connection import Database
from photosort.extractor.exiftool import ExiftoolRunner, ExiftoolResult
from photosort.extractor.parser import (
    extract_metadata_families,
    get_first_value,
    metadata_to_json,
    parse_exif_date,
)
from photosort.extractor.strategies import get_strategy


logger = logging.getLogger(__name__)

# Minimum file size for metadata extraction (in bytes)
# Files smaller than this are likely corrupted or placeholder files.
# A typical JPEG thumbnail is ~10KB, smallest valid RAW is ~1MB.
# We use 10KB as a reasonable threshold that catches corrupted files
# while allowing small but valid images.
MIN_FILE_SIZE_BYTES = 10 * 1024  # 10 KB


@dataclass
class MetadataExtractorStats:
    """Statistics from metadata extraction run."""

    total_files: int = 0
    files_extracted: int = 0
    files_with_date_original: int = 0
    files_with_gps: int = 0
    files_failed: int = 0
    files_skipped: int = 0
    start_time: float = field(default_factory=time.time)


class MetadataExtractor:
    """Extracts metadata from files using exiftool."""

    def __init__(
        self,
        database: Database,
        batch_size: int = 100,
    ) -> None:
        self.db = database
        self.batch_size = batch_size
        self.exiftool = ExiftoolRunner()

    def extract_all(
        self,
        strategy: str = "selective",
        limit: int | None = None,
    ) -> MetadataExtractorStats:
        """Extract metadata for files based on strategy."""
        stats = MetadataExtractorStats()
        strat = get_strategy(strategy)

        file_ids = strat.get_file_ids(self.db.conn, limit)
        total_to_process = len(file_ids)

        logger.info(
            "Starting metadata extraction (strategy: %s, files: %d)",
            strat.name,
            total_to_process,
        )

        for i in range(0, len(file_ids), self.batch_size):
            batch_ids = file_ids[i : i + self.batch_size]
            self._process_batch(batch_ids, stats)

            logger.info(
                "[%d/%d] Processed (%.1f files/sec)",
                stats.total_files,
                total_to_process,
                stats.total_files / max(1, time.time() - stats.start_time),
            )

        return stats

    def _process_batch(self, file_ids: list[int], stats: MetadataExtractorStats) -> None:
        """Process a batch of files."""
        file_info = self._fetch_file_paths(file_ids)
        if not file_info:
            return

        # Separate files to extract vs skip based on size
        files_to_extract = []
        skipped_updates = []

        for info in file_info:
            stats.total_files += 1
            if info["size"] < MIN_FILE_SIZE_BYTES:
                # Skip small files - likely corrupted
                skip_reason = f"file_too_small:{info["size"]}_bytes"
                skipped_updates.append(self._build_skip_update(info["id"], skip_reason))
                stats.files_skipped += 1
            else:
                files_to_extract.append(info)

        # Insert skip records immediately
        if skipped_updates:
            self._batch_insert(skipped_updates)

        # Extract metadata for remaining files
        if not files_to_extract:
            return

        paths = [info["source_path"] for info in files_to_extract]
        results = self.exiftool.extract_batch(paths)

        result_by_path = {r.source_file: r for r in results}
        updates = []

        for info in files_to_extract:
            result = result_by_path.get(info["source_path"])
            if result:
                update = self._build_update(info["id"], result)
                updates.append(update)
                self._update_stats(stats, update)
            else:
                updates.append(self._build_error_update(info["id"], "No exiftool result"))
                stats.files_failed += 1

        self._batch_insert(updates)

    def _fetch_file_paths(self, file_ids: list[int]) -> list[dict]:
        """Fetch source paths and sizes for file IDs, returning absolute paths."""
        placeholders = ",".join("?" for _ in file_ids)
        # Join with scan_sessions to get source_root and construct absolute path
        query = f"""
            SELECT f.id, f.source_path, f.size, s.source_root
            FROM files f
            JOIN scan_sessions s ON f.scan_session_id = s.id
            WHERE f.id IN ({placeholders})
        """
        cursor = self.db.conn.execute(query, file_ids)
        results = []
        for row in cursor.fetchall():
            source_root = row["source_root"]
            relative_path = row["source_path"]
            # Construct absolute path
            absolute_path = f"{source_root}/{relative_path}" if relative_path else source_root
            results.append(
                {
                    "id": row["id"],
                    "source_path": absolute_path,
                    "size": row["size"],
                }
            )
        return results

    def _build_update(self, file_id: int, result: ExiftoolResult) -> dict:
        """Build update dict from exiftool result."""
        now_unix = time.time()
        now_int = int(now_unix)

        if result.error:
            return self._build_error_update(file_id, result.error)

        meta = result.metadata
        date_original_unix, date_original = self._extract_date_original(meta)
        date_digitized_unix, date_digitized = self._extract_date_digitized(meta)
        date_modify_unix, date_modify = self._extract_date_modify(meta)

        return {
            "file_id": file_id,
            "date_original_unix": date_original_unix,
            "date_original": date_original,
            "date_digitized_unix": date_digitized_unix,
            "date_digitized": date_digitized,
            "date_modify_unix": date_modify_unix,
            "date_modify": date_modify,
            "make": get_first_value(meta, "EXIF:Make", "QuickTime:Make", "XMP:Make"),
            "model": get_first_value(meta, "EXIF:Model", "QuickTime:Model", "XMP:Model"),
            "lens_model": get_first_value(meta, "EXIF:LensModel", "EXIF:Lens", "XMP:Lens"),
            "image_width": get_first_value(
                meta, "EXIF:ImageWidth", "EXIF:ExifImageWidth", "QuickTime:ImageWidth"
            ),
            "image_height": get_first_value(
                meta, "EXIF:ImageHeight", "EXIF:ExifImageHeight", "QuickTime:ImageHeight"
            ),
            "orientation": get_first_value(meta, "EXIF:Orientation"),
            "duration_seconds": get_first_value(meta, "QuickTime:Duration", "Matroska:Duration"),
            "video_frame_rate": get_first_value(
                meta, "QuickTime:VideoFrameRate", "Matroska:FrameRate"
            ),
            "gps_latitude": get_first_value(meta, "EXIF:GPSLatitude", "Composite:GPSLatitude"),
            "gps_longitude": get_first_value(meta, "EXIF:GPSLongitude", "Composite:GPSLongitude"),
            "gps_altitude": get_first_value(meta, "EXIF:GPSAltitude"),
            "mime_type": get_first_value(meta, "File:MIMEType"),
            "metadata_families": extract_metadata_families(meta),
            "metadata_json": metadata_to_json(meta),
            "extracted_at_unix": now_unix,
            "extracted_at": now_int,
            "extractor_version": self.exiftool.version,
            "extraction_error": None,
            "skip_reason": None,
        }

    def _build_error_update(self, file_id: int, error: str) -> dict:
        """Build update dict for failed extraction."""
        now_unix = time.time()
        now_int = int(now_unix)
        return {
            "file_id": file_id,
            "date_original_unix": None,
            "date_original": None,
            "date_digitized_unix": None,
            "date_digitized": None,
            "date_modify_unix": None,
            "date_modify": None,
            "make": None,
            "model": None,
            "lens_model": None,
            "image_width": None,
            "image_height": None,
            "orientation": None,
            "duration_seconds": None,
            "video_frame_rate": None,
            "gps_latitude": None,
            "gps_longitude": None,
            "gps_altitude": None,
            "mime_type": None,
            "metadata_families": None,
            "metadata_json": None,
            "extracted_at_unix": now_unix,
            "extracted_at": now_int,
            "extractor_version": self.exiftool.version,
            "extraction_error": error,
            "skip_reason": None,
        }

    def _build_skip_update(self, file_id: int, skip_reason: str) -> dict:
        """Build update dict for skipped file."""
        now_unix = time.time()
        now_int = int(now_unix)
        return {
            "file_id": file_id,
            "date_original_unix": None,
            "date_original": None,
            "date_digitized_unix": None,
            "date_digitized": None,
            "date_modify_unix": None,
            "date_modify": None,
            "make": None,
            "model": None,
            "lens_model": None,
            "image_width": None,
            "image_height": None,
            "orientation": None,
            "duration_seconds": None,
            "video_frame_rate": None,
            "gps_latitude": None,
            "gps_longitude": None,
            "gps_altitude": None,
            "mime_type": None,
            "metadata_families": None,
            "metadata_json": None,
            "extracted_at_unix": now_unix,
            "extracted_at": now_int,
            "extractor_version": self.exiftool.version,
            "extraction_error": None,
            "skip_reason": skip_reason,
        }

    def _extract_date_original(self, meta: dict) -> tuple[float | None, int | None]:
        date_str = get_first_value(
            meta,
            "EXIF:DateTimeOriginal",
            "QuickTime:CreateDate",
            "XMP:DateTimeOriginal",
        )
        return parse_exif_date(date_str)

    def _extract_date_digitized(self, meta: dict) -> tuple[float | None, int | None]:
        date_str = get_first_value(
            meta,
            "EXIF:DateTimeDigitized",
            "QuickTime:MediaCreateDate",
            "XMP:CreateDate",
        )
        return parse_exif_date(date_str)

    def _extract_date_modify(self, meta: dict) -> tuple[float | None, int | None]:
        date_str = get_first_value(
            meta,
            "EXIF:ModifyDate",
            "QuickTime:ModifyDate",
            "XMP:ModifyDate",
        )
        return parse_exif_date(date_str)

    def _update_stats(self, stats: MetadataExtractorStats, update: dict) -> None:
        if update.get("extraction_error"):
            stats.files_failed += 1
        else:
            stats.files_extracted += 1
            if update.get("date_original"):
                stats.files_with_date_original += 1
            if update.get("gps_latitude"):
                stats.files_with_gps += 1

    def _batch_insert(self, updates: list[dict]) -> None:
        """Insert metadata records into database."""
        query = """
            INSERT INTO file_metadata (
                file_id, date_original_unix, date_original,
                date_digitized_unix, date_digitized, date_modify_unix, date_modify,
                make, model, lens_model,
                image_width, image_height, orientation,
                duration_seconds, video_frame_rate,
                gps_latitude, gps_longitude, gps_altitude,
                mime_type, metadata_families, metadata_json,
                extracted_at_unix, extracted_at, extractor_version,
                extraction_error, skip_reason
            ) VALUES (
                :file_id, :date_original_unix, :date_original,
                :date_digitized_unix, :date_digitized, :date_modify_unix, :date_modify,
                :make, :model, :lens_model,
                :image_width, :image_height, :orientation,
                :duration_seconds, :video_frame_rate,
                :gps_latitude, :gps_longitude, :gps_altitude,
                :mime_type, :metadata_families, :metadata_json,
                :extracted_at_unix, :extracted_at, :extractor_version,
                :extraction_error, :skip_reason
            )
        """
        self.db.conn.executemany(query, updates)
        self.db.conn.commit()

    def get_stats(self) -> dict:
        """Get extraction statistics from database."""
        cursor = self.db.conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN extraction_error IS NULL AND skip_reason IS NULL
                    THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN extraction_error IS NOT NULL THEN 1 ELSE 0 END) as errors,
                SUM(CASE WHEN skip_reason IS NOT NULL THEN 1 ELSE 0 END) as skipped,
                SUM(CASE WHEN date_original IS NOT NULL THEN 1 ELSE 0 END) as with_date,
                SUM(CASE WHEN gps_latitude IS NOT NULL THEN 1 ELSE 0 END) as with_gps
            FROM file_metadata
        """
        )
        row = cursor.fetchone()
        return dict(row) if row else {}
