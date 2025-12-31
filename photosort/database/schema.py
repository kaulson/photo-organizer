"""Database schema definition."""

import sqlite3

SCHEMA_SQL = """
-- Scan session tracking
CREATE TABLE IF NOT EXISTS scan_sessions (
    id INTEGER PRIMARY KEY,
    source_root TEXT NOT NULL,
    source_drive_uuid TEXT NOT NULL,
    started_at_unix REAL NOT NULL,
    started_at INTEGER NOT NULL,
    completed_at_unix REAL,
    completed_at INTEGER,
    status TEXT NOT NULL,
    error_message TEXT,
    files_scanned INTEGER DEFAULT 0,
    directories_scanned INTEGER DEFAULT 0,
    total_bytes INTEGER DEFAULT 0,
    UNIQUE(source_root)
);

-- Directory completion tracking (for resumability)
CREATE TABLE IF NOT EXISTS completed_directories (
    id INTEGER PRIMARY KEY,
    scan_session_id INTEGER NOT NULL REFERENCES scan_sessions(id) ON DELETE CASCADE,
    directory_path TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    completed_at_unix REAL NOT NULL,
    completed_at INTEGER NOT NULL,
    UNIQUE(scan_session_id, directory_path)
);

-- File inventory
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    scan_session_id INTEGER NOT NULL REFERENCES scan_sessions(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    directory_path TEXT NOT NULL,
    filename_full TEXT NOT NULL,
    filename_base TEXT NOT NULL,
    extension TEXT,
    size INTEGER NOT NULL,
    fs_modified_at_unix REAL,
    fs_modified_at INTEGER,
    fs_changed_at_unix REAL,
    fs_changed_at INTEGER,
    fs_created_at_unix REAL,
    fs_created_at INTEGER,
    fs_accessed_at_unix REAL,
    fs_accessed_at INTEGER,
    hash_quick_start TEXT,
    hash_quick_end TEXT,
    hash_full TEXT,
    date_exif_original_unix REAL,
    date_exif_original INTEGER,
    date_exif_create_unix REAL,
    date_exif_create INTEGER,
    date_exif_modify_unix REAL,
    date_exif_modify INTEGER,

    -- Path-based date extraction (Pass 1)
    date_path_hierarchy INTEGER,
    date_path_hierarchy_source TEXT,
    date_path_folder INTEGER,
    date_path_folder_source TEXT,
    date_path_filename INTEGER,
    date_path_filename_source TEXT,

    -- Resolved path date
    date_path_resolved INTEGER,
    date_path_resolved_source TEXT,

    -- Final resolved date (after all passes)
    date_resolved INTEGER,
    date_resolved_source TEXT,

    file_type TEXT,
    exif_make TEXT,
    exif_model TEXT,
    metadata_json TEXT,
    scanned_at_unix REAL NOT NULL,
    scanned_at INTEGER NOT NULL,
    metadata_extracted_at_unix REAL,
    metadata_extracted_at INTEGER,
    classified_at_unix REAL,
    classified_at INTEGER,
    date_resolved_at_unix REAL,
    date_resolved_at INTEGER,
    UNIQUE(scan_session_id, source_path)
);

-- Indexes for scanner operations
CREATE INDEX IF NOT EXISTS idx_files_session ON files(scan_session_id);
CREATE INDEX IF NOT EXISTS idx_files_directory ON files(scan_session_id, directory_path);
CREATE INDEX IF NOT EXISTS idx_completed_dirs_session ON completed_directories(scan_session_id);

-- Indexes for later phases
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension) WHERE extension IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_files_hash_quick
    ON files(hash_quick_start) WHERE hash_quick_start IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_files_hash_full ON files(hash_full) WHERE hash_full IS NOT NULL;

-- Indexes for date resolution
CREATE INDEX IF NOT EXISTS idx_files_date_path_hierarchy
    ON files(date_path_hierarchy) WHERE date_path_hierarchy IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_files_date_path_folder
    ON files(date_path_folder) WHERE date_path_folder IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_files_date_path_filename
    ON files(date_path_filename) WHERE date_path_filename IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_files_no_path_date ON files(scan_session_id)
    WHERE date_path_hierarchy IS NULL
    AND date_path_folder IS NULL
    AND date_path_filename IS NULL;

-- Metadata extraction results (separate table per spec)
CREATE TABLE IF NOT EXISTS file_metadata (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,

    -- Core dates (exiftool normalizes these across formats)
    date_original_unix REAL,
    date_original INTEGER,
    date_digitized_unix REAL,
    date_digitized INTEGER,
    date_modify_unix REAL,
    date_modify INTEGER,

    -- Camera/device info
    make TEXT,
    model TEXT,
    lens_model TEXT,

    -- Dimensions
    image_width INTEGER,
    image_height INTEGER,
    orientation INTEGER,

    -- Video-specific
    duration_seconds REAL,
    video_frame_rate REAL,

    -- GPS
    gps_latitude REAL,
    gps_longitude REAL,
    gps_altitude REAL,

    -- Format info
    mime_type TEXT,
    metadata_families TEXT,

    -- Full dump (filtered, no binary data)
    metadata_json TEXT,

    -- Extraction tracking
    extracted_at_unix REAL NOT NULL,
    extracted_at INTEGER NOT NULL,
    extractor_version TEXT,
    extraction_error TEXT,
    skip_reason TEXT
);

-- Indexes for file_metadata
CREATE INDEX IF NOT EXISTS idx_file_metadata_file_id ON file_metadata(file_id);
CREATE INDEX IF NOT EXISTS idx_file_metadata_date_original
    ON file_metadata(date_original) WHERE date_original IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_file_metadata_make_model
    ON file_metadata(make, model) WHERE make IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_file_metadata_has_gps
    ON file_metadata(file_id) WHERE gps_latitude IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_file_metadata_errors
    ON file_metadata(file_id) WHERE extraction_error IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_file_metadata_skipped
    ON file_metadata(file_id) WHERE skip_reason IS NOT NULL;

-- Planner tables
-- Folder-level planning results
CREATE TABLE IF NOT EXISTS folder_plan (
    id INTEGER PRIMARY KEY,
    scan_session_id INTEGER NOT NULL,
    source_folder TEXT NOT NULL,

    -- Resolution result
    resolved_date INTEGER,                    -- YYYYMMDD, NULL if bucketed
    resolved_date_source TEXT,                -- 'path_date', 'prevalent_date',
                                              -- 'unanimous', 'inherited',
                                              -- 'low_coverage', 'wide_spread',
                                              -- 'no_consensus', 'no_images'
    target_folder TEXT NOT NULL,
    bucket TEXT,                              -- NULL, 'mixed_dates', 'non_media'
    annotation TEXT,                          -- Extracted annotation from folder name

    -- File counts
    total_file_count INTEGER NOT NULL,
    image_file_count INTEGER NOT NULL,
    images_with_date_count INTEGER NOT NULL,

    -- Coverage metrics
    date_coverage_pct REAL,                   -- images_with_date / image_file_count

    -- Date distribution (for images with dates)
    prevalent_date INTEGER,
    prevalent_date_count INTEGER,
    prevalent_date_pct REAL,                  -- prevalent_count / images_with_date
    unique_date_count INTEGER,
    min_date INTEGER,
    max_date INTEGER,
    date_span_months INTEGER,

    -- Inheritance
    inherited_from_folder_id INTEGER REFERENCES folder_plan(id),
    is_subfolder BOOLEAN DEFAULT FALSE,

    -- Thresholds used (for reproducibility)
    config_min_coverage REAL,
    config_min_prevalence REAL,
    config_max_span_months INTEGER,

    -- Timestamps
    planned_at_unix REAL NOT NULL,
    planned_at INTEGER NOT NULL,

    UNIQUE(scan_session_id, source_folder)
);

CREATE INDEX IF NOT EXISTS idx_folder_plan_session
    ON folder_plan(scan_session_id);
CREATE INDEX IF NOT EXISTS idx_folder_plan_bucket
    ON folder_plan(bucket) WHERE bucket IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_folder_plan_resolved_date
    ON folder_plan(resolved_date) WHERE resolved_date IS NOT NULL;

-- File-level planning results
CREATE TABLE IF NOT EXISTS file_plan (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,
    folder_plan_id INTEGER NOT NULL REFERENCES folder_plan(id) ON DELETE CASCADE,

    -- Source (denormalized for easy querying)
    source_path TEXT NOT NULL,
    source_filename TEXT NOT NULL,

    -- File's own resolved date (before folder analysis)
    file_resolved_date INTEGER,               -- YYYYMMDD
    file_date_source TEXT,                    -- 'path_folder', 'path_filename',
                                              -- 'exif', 'fs_modified', 'none'

    -- Target
    target_folder TEXT NOT NULL,
    target_path TEXT NOT NULL,                -- Full path including filename
    target_filename TEXT NOT NULL,            -- May differ from source if duplicate

    -- Flags
    is_potential_duplicate BOOLEAN DEFAULT FALSE,
    duplicate_source_hash TEXT,               -- Short hash used in filename
    is_sidecar BOOLEAN DEFAULT FALSE,

    -- Debugging
    resolution_reason TEXT,                   -- Human-readable explanation

    -- Timestamps
    planned_at_unix REAL NOT NULL,
    planned_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_plan_file_id ON file_plan(file_id);
CREATE INDEX IF NOT EXISTS idx_file_plan_folder_id ON file_plan(folder_plan_id);
CREATE INDEX IF NOT EXISTS idx_file_plan_target ON file_plan(target_path);
CREATE INDEX IF NOT EXISTS idx_file_plan_duplicates
    ON file_plan(file_id) WHERE is_potential_duplicate = TRUE;
CREATE INDEX IF NOT EXISTS idx_file_plan_sidecars
    ON file_plan(file_id) WHERE is_sidecar = TRUE;
"""


def create_schema(conn: sqlite3.Connection) -> None:
    """Create schema and run migrations."""
    # Check if files table exists (i.e., existing database)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
    files_exists = cursor.fetchone() is not None

    if files_exists:
        # Run migrations first for existing databases
        migrate_add_date_columns(conn)
        migrate_add_skip_reason_column(conn)

    # Now run the full schema (CREATE IF NOT EXISTS is safe)
    conn.executescript(SCHEMA_SQL)
    conn.commit()


MIGRATION_ADD_DATE_COLUMNS = """
-- Add date resolution columns if they don't exist
ALTER TABLE files ADD COLUMN date_path_hierarchy INTEGER;
ALTER TABLE files ADD COLUMN date_path_hierarchy_source TEXT;
ALTER TABLE files ADD COLUMN date_path_folder INTEGER;
ALTER TABLE files ADD COLUMN date_path_folder_source TEXT;
ALTER TABLE files ADD COLUMN date_path_filename INTEGER;
ALTER TABLE files ADD COLUMN date_path_filename_source TEXT;
ALTER TABLE files ADD COLUMN date_path_resolved INTEGER;
ALTER TABLE files ADD COLUMN date_path_resolved_source TEXT;
ALTER TABLE files ADD COLUMN date_resolved INTEGER;
ALTER TABLE files ADD COLUMN date_resolved_source TEXT;
ALTER TABLE files ADD COLUMN date_resolved_at_unix REAL;
ALTER TABLE files ADD COLUMN date_resolved_at INTEGER;
"""


def migrate_add_date_columns(conn: sqlite3.Connection) -> None:
    """Add date resolution columns to existing database."""
    cursor = conn.execute("PRAGMA table_info(files)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    new_columns = [
        ("date_path_hierarchy", "INTEGER"),
        ("date_path_hierarchy_source", "TEXT"),
        ("date_path_folder", "INTEGER"),
        ("date_path_folder_source", "TEXT"),
        ("date_path_filename", "INTEGER"),
        ("date_path_filename_source", "TEXT"),
        ("date_path_resolved", "INTEGER"),
        ("date_path_resolved_source", "TEXT"),
        ("date_resolved", "INTEGER"),
        ("date_resolved_source", "TEXT"),
        ("date_resolved_at_unix", "REAL"),
        ("date_resolved_at", "INTEGER"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE files ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass  # Column might already exist

    conn.commit()


def migrate_add_skip_reason_column(conn: sqlite3.Connection) -> None:
    """Add skip_reason column to file_metadata table if it doesn't exist."""
    cursor = conn.execute("PRAGMA table_info(file_metadata)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if "skip_reason" not in existing_columns:
        try:
            conn.execute("ALTER TABLE file_metadata ADD COLUMN skip_reason TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column might already exist or table doesn't exist yet
