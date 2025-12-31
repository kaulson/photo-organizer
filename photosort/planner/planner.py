"""Main Planner class that orchestrates folder analysis and file planning."""

import time
from pathlib import Path

from photosort.database import Database
from photosort.planner.analyzer import FolderDateAnalysis, analyze_folder, is_image_extension
from photosort.planner.path_builder import (
    build_bucket_path,
    build_target_folder,
    extract_annotation,
    resolve_filename_duplicate,
)
from photosort.planner.resolver import (
    FileDateResult,
    PlannerConfig,
    resolve_file_date,
    resolve_folder,
    resolve_folder_with_path_date,
)
from photosort.planner.sidecar import detect_sidecar


class Planner:
    """Orchestrates planning of file target locations.

    The Planner analyzes scanned files and determines where each file
    should be placed in the target directory structure. It operates at
    the folder level, resolving a date for each folder, then deriving
    target paths for all files within.

    The Planner does not move or copy files. It produces a plan stored
    in the folder_plan and file_plan database tables.
    """

    def __init__(self, db: Database, config: PlannerConfig | None = None) -> None:
        """Initialize the Planner.

        Args:
            db: Database connection.
            config: Optional planner configuration. Uses defaults if not provided.
        """
        self.db = db
        self.config = config or PlannerConfig()

    def plan(self, scan_session_id: int) -> None:
        """Generate a plan for all files in a scan session.

        This clears any existing plan for the session and rebuilds it.

        Args:
            scan_session_id: ID of the scan session to plan.
        """
        # Clear existing plan
        self._clear_existing_plan(scan_session_id)

        # Get all unique folders
        folders = self._get_folders(scan_session_id)

        # Process folders in depth order (shallowest first for inheritance)
        sorted_folders = sorted(folders, key=lambda f: f.count("/"))

        # Track filenames per target folder for duplicate detection across source folders
        # Key: target_folder, Value: set of filenames already used
        target_filenames: dict[str, set[str]] = {}

        # Resolve each folder
        for folder in sorted_folders:
            self._process_folder(scan_session_id, folder, target_filenames)

    def _clear_existing_plan(self, scan_session_id: int) -> None:
        """Clear any existing plan for this session."""
        # Get folder_plan IDs for this session
        cursor = self.db.conn.execute(
            "SELECT id FROM folder_plan WHERE scan_session_id = ?",
            (scan_session_id,),
        )
        folder_plan_ids = [row["id"] for row in cursor.fetchall()]

        # Delete file_plan entries (foreign key might not cascade)
        if folder_plan_ids:
            placeholders = ",".join("?" * len(folder_plan_ids))
            self.db.conn.execute(
                f"DELETE FROM file_plan WHERE folder_plan_id IN ({placeholders})",  # noqa: S608
                folder_plan_ids,
            )

        # Delete folder_plan entries
        self.db.conn.execute(
            "DELETE FROM folder_plan WHERE scan_session_id = ?",
            (scan_session_id,),
        )
        self.db.conn.commit()

    def _get_folders(self, scan_session_id: int) -> list[str]:
        """Get all unique folder paths in a scan session."""
        cursor = self.db.conn.execute(
            "SELECT DISTINCT directory_path FROM files WHERE scan_session_id = ?",
            (scan_session_id,),
        )
        return [row["directory_path"] for row in cursor.fetchall()]

    def _process_folder(
        self, scan_session_id: int, folder: str, target_filenames: dict[str, set[str]]
    ) -> None:
        """Process a single folder: analyze, resolve, and create plan entries."""
        # Get all files in folder
        cursor = self.db.conn.execute(
            """
            SELECT f.id, f.source_path, f.filename_full, f.filename_base, f.extension,
                   f.date_path_folder, f.date_path_filename, f.fs_modified_at_unix,
                   fm.date_original
            FROM files f
            LEFT JOIN file_metadata fm ON f.id = fm.file_id
            WHERE f.scan_session_id = ? AND f.directory_path = ?
            """,
            (scan_session_id, folder),
        )
        files = [dict(row) for row in cursor.fetchall()]

        if not files:
            return

        # Resolve dates for each file
        file_dates: list[dict] = []
        for f in files:
            date_result = resolve_file_date(
                date_path_folder=f["date_path_folder"],
                date_path_filename=f["date_path_filename"],
                date_exif=f["date_original"],
                fs_modified_unix=f["fs_modified_at_unix"],
            )
            file_dates.append(
                {
                    "file": f,
                    "date_result": date_result,
                    "is_image": is_image_extension(f["extension"]),
                }
            )

        # Check for path-derived date in any file
        path_date = None
        for fd in file_dates:
            if fd["file"]["date_path_folder"]:
                path_date = fd["file"]["date_path_folder"]
                break

        # Resolve folder
        if path_date:
            folder_resolution = resolve_folder_with_path_date(path_date)
            analysis = self._compute_analysis(file_dates)
        else:
            # Statistical analysis
            analysis_data = [
                {"date": fd["date_result"].date, "is_image": fd["is_image"]} for fd in file_dates
            ]
            analysis = analyze_folder(analysis_data)
            folder_resolution = resolve_folder(analysis, self.config)

        # Build target folder path
        if folder_resolution.bucket:
            target_folder = build_bucket_path(folder_resolution.bucket, folder)
            annotation = None
        else:
            folder_name = Path(folder).name if folder else ""
            annotation = extract_annotation(folder_name, folder_resolution.resolved_date or 0)
            target_folder = build_target_folder(
                folder_resolution.resolved_date or 0,
                annotation,
            )

        # Insert folder_plan
        now_unix = time.time()
        now_int = int(now_unix)

        cursor = self.db.conn.execute(
            """
            INSERT INTO folder_plan (
                scan_session_id, source_folder, resolved_date, resolved_date_source,
                target_folder, bucket, annotation,
                total_file_count, image_file_count, images_with_date_count,
                date_coverage_pct, prevalent_date, prevalent_date_count,
                prevalent_date_pct, unique_date_count, min_date, max_date,
                date_span_months,
                config_min_coverage, config_min_prevalence, config_max_span_months,
                planned_at_unix, planned_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_session_id,
                folder,
                folder_resolution.resolved_date,
                folder_resolution.source,
                target_folder,
                folder_resolution.bucket,
                annotation,
                analysis.total_files,
                analysis.image_files,
                analysis.images_with_date,
                analysis.date_coverage_pct,
                analysis.prevalent_date,
                analysis.prevalent_date_count,
                analysis.prevalent_date_pct,
                analysis.unique_date_count,
                analysis.min_date,
                analysis.max_date,
                analysis.date_span_months,
                self.config.min_coverage_threshold,
                self.config.min_prevalence_threshold,
                self.config.max_date_span_months,
                now_unix,
                now_int,
            ),
        )
        folder_plan_id = cursor.lastrowid

        # Build folder file list for sidecar detection
        folder_file_info = [
            {"filename_base": fd["file"]["filename_base"], "extension": fd["file"]["extension"]}
            for fd in file_dates
        ]

        # Get or create the set of existing filenames for this target folder
        if target_folder not in target_filenames:
            target_filenames[target_folder] = set()
        existing_filenames = target_filenames[target_folder]

        # Insert file_plan for each file
        for fd in file_dates:
            f = fd["file"]
            file_date: FileDateResult = fd["date_result"]

            # Check for sidecar
            is_sidecar = detect_sidecar(
                filename_base=f["filename_base"],
                extension=f["extension"],
                folder_files=folder_file_info,
            )

            # Handle duplicates (check against all files going to this target folder)
            dup_result = resolve_filename_duplicate(
                f["filename_full"],
                f["source_path"],
                existing_filenames,
            )
            # Add to the global tracking set
            existing_filenames.add(dup_result.filename)

            # Build full target path
            target_path = f"{target_folder}/{dup_result.filename}"

            self.db.conn.execute(
                """
                INSERT INTO file_plan (
                    file_id, folder_plan_id, source_path, source_filename,
                    file_resolved_date, file_date_source,
                    target_folder, target_path, target_filename,
                    is_potential_duplicate, duplicate_source_hash, is_sidecar,
                    planned_at_unix, planned_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f["id"],
                    folder_plan_id,
                    f["source_path"],
                    f["filename_full"],
                    file_date.date,
                    file_date.source,
                    target_folder,
                    target_path,
                    dup_result.filename,
                    dup_result.is_duplicate,
                    dup_result.source_hash,
                    is_sidecar,
                    now_unix,
                    now_int,
                ),
            )

        self.db.conn.commit()

    def _compute_analysis(self, file_dates: list[dict]) -> FolderDateAnalysis:
        """Compute folder analysis from already-resolved file dates."""
        analysis_data = [
            {"date": fd["date_result"].date, "is_image": fd["is_image"]} for fd in file_dates
        ]
        return analyze_folder(analysis_data)
