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
    date_path_derived_unix REAL,
    date_path_derived INTEGER,
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
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
