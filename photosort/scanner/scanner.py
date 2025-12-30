"""Main scanner implementation."""

import time
from pathlib import Path

from photosort.database import Database, ScanStatus
from photosort.scanner.filesystem import FileInfo, walk_directory
from photosort.scanner.progress import ProgressReporter, ScanStats
from photosort.scanner.uuid import get_drive_uuid


class Scanner:
    """Scans filesystem and stores file metadata in database."""

    def __init__(
        self,
        db: Database,
        progress_interval: int = 1000,
        max_path_length: int = 4096,
    ):
        self.db = db
        self.progress = ProgressReporter(interval=progress_interval)
        self.max_path_length = max_path_length

    def scan(self, source_root: Path, resume: bool = False) -> ScanStats:
        source_root = source_root.resolve()
        drive_uuid = get_drive_uuid(source_root)

        print(f"Starting scan of {source_root}")
        print(f"Drive UUID: {drive_uuid}")

        if resume:
            session_id, completed_dirs, stats = self._resume_session(source_root)
            if session_id is None:
                raise ValueError(f"No interrupted scan found for {source_root}")
            self.progress.report_resume(stats.files_scanned, stats.directories_scanned)
        else:
            self._delete_existing_session(source_root)
            session_id = self._create_session(source_root, drive_uuid)
            completed_dirs = set()
            stats = ScanStats()
            print("Previous scan data will be overwritten.")

        try:
            self._scan_filesystem(source_root, session_id, completed_dirs, stats)
            self._complete_session(session_id, stats)
            self.progress.report_completion(stats)
        except KeyboardInterrupt:
            self.progress.report_interruption(stats)
            raise

        return stats

    def _scan_filesystem(
        self,
        source_root: Path,
        session_id: int,
        completed_dirs: set[str],
        stats: ScanStats,
    ) -> None:
        for batch in walk_directory(source_root, completed_dirs, self.max_path_length):
            self._delete_partial_directory(session_id, batch.directory_path)
            self._insert_files(session_id, batch.files)
            self._mark_directory_complete(session_id, batch.directory_path, batch.files)

            stats.files_scanned += len(batch.files)
            stats.directories_scanned += 1
            stats.total_bytes += sum(f.size for f in batch.files)

            self._update_session_stats(session_id, stats)
            self.progress.report_if_needed(stats, batch.directory_path)

    def _resume_session(self, source_root: Path) -> tuple[int | None, set[str], ScanStats]:
        row = self.db.conn.execute(
            """
            SELECT id, files_scanned, directories_scanned, total_bytes, started_at_unix
            FROM scan_sessions
            WHERE source_root = ? AND status = ?
            """,
            (str(source_root), ScanStatus.RUNNING.value),
        ).fetchone()

        if not row:
            return None, set(), ScanStats()

        session_id = row["id"]
        completed_dirs = self._get_completed_directories(session_id)

        stats = ScanStats(
            files_scanned=row["files_scanned"],
            directories_scanned=row["directories_scanned"],
            total_bytes=row["total_bytes"],
            start_time=row["started_at_unix"],
        )

        return session_id, completed_dirs, stats

    def _get_completed_directories(self, session_id: int) -> set[str]:
        rows = self.db.conn.execute(
            "SELECT directory_path FROM completed_directories WHERE scan_session_id = ?",
            (session_id,),
        ).fetchall()
        return {row["directory_path"] for row in rows}

    def _delete_existing_session(self, source_root: Path) -> None:
        self.db.conn.execute(
            "DELETE FROM scan_sessions WHERE source_root = ?",
            (str(source_root),),
        )
        self.db.conn.commit()

    def _create_session(self, source_root: Path, drive_uuid: str) -> int:
        now = time.time()
        cursor = self.db.conn.execute(
            """
            INSERT INTO scan_sessions
            (source_root, source_drive_uuid, started_at_unix, started_at, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(source_root), drive_uuid, now, int(now), ScanStatus.RUNNING.value),
        )
        self.db.conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def _delete_partial_directory(self, session_id: int, directory_path: str) -> None:
        self.db.conn.execute(
            "DELETE FROM files WHERE scan_session_id = ? AND directory_path = ?",
            (session_id, directory_path),
        )

    def _insert_files(self, session_id: int, files: list[FileInfo]) -> None:
        now = time.time()
        rows = [
            (
                session_id,
                f.relative_path,
                f.directory_path,
                f.parsed_filename.full,
                f.parsed_filename.base,
                f.parsed_filename.extension,
                f.size,
                f.stat_result.st_mtime,
                int(f.stat_result.st_mtime),
                f.stat_result.st_ctime,
                int(f.stat_result.st_ctime),
                _get_birthtime(f.stat_result),
                _get_birthtime_int(f.stat_result),
                f.stat_result.st_atime,
                int(f.stat_result.st_atime),
                now,
                int(now),
            )
            for f in files
        ]

        self.db.conn.executemany(
            """
            INSERT INTO files (
                scan_session_id, source_path, directory_path,
                filename_full, filename_base, extension, size,
                fs_modified_at_unix, fs_modified_at,
                fs_changed_at_unix, fs_changed_at,
                fs_created_at_unix, fs_created_at,
                fs_accessed_at_unix, fs_accessed_at,
                scanned_at_unix, scanned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _mark_directory_complete(
        self,
        session_id: int,
        directory_path: str,
        files: list[FileInfo],
    ) -> None:
        now = time.time()
        total_bytes = sum(f.size for f in files)

        self.db.conn.execute(
            """
            INSERT OR REPLACE INTO completed_directories
            (scan_session_id, directory_path, file_count, total_bytes,
             completed_at_unix, completed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, directory_path, len(files), total_bytes, now, int(now)),
        )
        self.db.conn.commit()

    def _update_session_stats(self, session_id: int, stats: ScanStats) -> None:
        self.db.conn.execute(
            """
            UPDATE scan_sessions
            SET files_scanned = ?, directories_scanned = ?, total_bytes = ?
            WHERE id = ?
            """,
            (stats.files_scanned, stats.directories_scanned, stats.total_bytes, session_id),
        )

    def _complete_session(self, session_id: int, stats: ScanStats) -> None:
        now = time.time()
        self.db.conn.execute(
            """
            UPDATE scan_sessions
            SET status = ?, completed_at_unix = ?, completed_at = ?,
                files_scanned = ?, directories_scanned = ?, total_bytes = ?
            WHERE id = ?
            """,
            (
                ScanStatus.COMPLETED.value,
                now,
                int(now),
                stats.files_scanned,
                stats.directories_scanned,
                stats.total_bytes,
                session_id,
            ),
        )
        self.db.conn.commit()


def _get_birthtime(stat_result) -> float | None:
    try:
        return stat_result.st_birthtime
    except AttributeError:
        return None


def _get_birthtime_int(stat_result) -> int | None:
    try:
        return int(stat_result.st_birthtime)
    except AttributeError:
        return None
