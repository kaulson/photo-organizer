"""DateResolver class for batch processing files from the database."""

import logging
import time
from dataclasses import dataclass

from photosort.database.connection import Database
from photosort.resolver import (
    DateExtraction,
    extract_filename_date,
    extract_folder_date,
    extract_hierarchy_date,
)


logger = logging.getLogger(__name__)


@dataclass
class ResolverStats:
    """Statistics from a date resolution run."""

    total_files: int = 0
    files_with_hierarchy: int = 0
    files_with_folder: int = 0
    files_with_filename: int = 0
    files_resolved: int = 0


class DateResolver:
    """Resolves dates for files in the database using path-based strategies."""

    def __init__(self, database: Database, batch_size: int = 1000) -> None:
        """Initialize resolver with database connection."""
        self.db = database
        self.batch_size = batch_size

    def resolve_all(self, reprocess: bool = False) -> ResolverStats:
        """
        Resolve dates for all files in the database.

        Args:
            reprocess: If True, reprocess all files. If False, only unprocessed.
        """
        stats = ResolverStats()

        if reprocess:
            offset = 0
        else:
            offset = None  # Use None to signal "no offset" mode

        while True:
            files = self._fetch_batch(offset, reprocess)
            if not files:
                break

            updates = []
            for file_id, relative_path, filename in files:
                stats.total_files += 1
                update = self._process_file(file_id, relative_path, filename, stats)
                updates.append(update)

            self._batch_update(updates)

            if reprocess:
                offset += self.batch_size  # type: ignore[operator]

            logger.info(
                "Processed %d files, %d resolved so far",
                stats.total_files,
                stats.files_resolved,
            )

        return stats

    def _fetch_batch(self, offset: int | None, reprocess: bool) -> list[tuple[int, str, str]]:
        """Fetch a batch of files to process."""
        if reprocess:
            query = """
                SELECT id, source_path, filename_full
                FROM files
                ORDER BY id
                LIMIT ? OFFSET ?
            """
            params: tuple = (self.batch_size, offset)
        else:
            # No offset needed - we filter by NULL which excludes processed rows
            query = """
                SELECT id, source_path, filename_full
                FROM files
                WHERE date_resolved_at_unix IS NULL
                ORDER BY id
                LIMIT ?
            """
            params = (self.batch_size,)

        cursor = self.db.conn.execute(query, params)
        return cursor.fetchall()

    def _process_file(
        self,
        file_id: int,
        relative_path: str,
        filename: str,
        stats: ResolverStats,
    ) -> dict:
        """Process a single file and return update parameters."""
        hierarchy = extract_hierarchy_date(relative_path)
        folder = extract_folder_date(relative_path)
        file_date = extract_filename_date(filename)

        if hierarchy.date_int:
            stats.files_with_hierarchy += 1
        if folder.date_int:
            stats.files_with_folder += 1
        if file_date.date_int:
            stats.files_with_filename += 1

        resolved = self._pick_best_date(hierarchy, folder, file_date)
        if resolved.date_int:
            stats.files_resolved += 1

        return {
            "id": file_id,
            "hierarchy_date": hierarchy.date_int,
            "hierarchy_source": hierarchy.source,
            "folder_date": folder.date_int,
            "folder_source": folder.source,
            "filename_date": file_date.date_int,
            "filename_source": file_date.source,
            "resolved_date": resolved.date_int,
            "resolved_source": resolved.source,
        }

    def _pick_best_date(
        self,
        hierarchy: DateExtraction,
        folder: DateExtraction,
        filename: DateExtraction,
    ) -> DateExtraction:
        """
        Pick the best date from the three strategies.

        Priority: hierarchy > folder > filename
        """
        if hierarchy.date_int:
            return DateExtraction(hierarchy.date_int, "hierarchy")
        if folder.date_int:
            return DateExtraction(folder.date_int, "folder")
        if filename.date_int:
            return DateExtraction(filename.date_int, "filename")
        return DateExtraction(None, None)

    def _batch_update(self, updates: list[dict]) -> None:
        """Batch update files in the database."""
        now_unix = int(time.time())
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now_unix))

        query = """
            UPDATE files SET
                date_path_hierarchy = ?,
                date_path_hierarchy_source = ?,
                date_path_folder = ?,
                date_path_folder_source = ?,
                date_path_filename = ?,
                date_path_filename_source = ?,
                date_path_resolved = ?,
                date_path_resolved_source = ?,
                date_resolved = ?,
                date_resolved_source = ?,
                date_resolved_at_unix = ?,
                date_resolved_at = ?
            WHERE id = ?
        """

        params_list = [
            (
                u["hierarchy_date"],
                u["hierarchy_source"],
                u["folder_date"],
                u["folder_source"],
                u["filename_date"],
                u["filename_source"],
                u["resolved_date"],
                u["resolved_source"],
                u["resolved_date"],  # date_resolved same as path_resolved for now
                u["resolved_source"],
                now_unix,
                now_str,
                u["id"],
            )
            for u in updates
        ]

        self.db.conn.executemany(query, params_list)
        self.db.conn.commit()
