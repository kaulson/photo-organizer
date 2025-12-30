"""Data models for the database."""

from dataclasses import dataclass
from enum import Enum


class ScanStatus(Enum):
    """Status of a scan session."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ScanSession:
    """Represents a scan session record."""

    id: int | None
    source_root: str
    source_drive_uuid: str
    started_at_unix: float
    started_at: int
    completed_at_unix: float | None
    completed_at: int | None
    status: ScanStatus
    error_message: str | None
    files_scanned: int
    directories_scanned: int
    total_bytes: int


@dataclass
class CompletedDirectory:
    """Represents a completed directory record for resumability."""

    id: int | None
    scan_session_id: int
    directory_path: str
    file_count: int
    total_bytes: int
    completed_at_unix: float
    completed_at: int


@dataclass
class FileRecord:
    """Represents a scanned file record."""

    id: int | None
    scan_session_id: int
    source_path: str
    directory_path: str
    filename_full: str
    filename_base: str
    extension: str | None
    size: int
    fs_modified_at_unix: float | None
    fs_modified_at: int | None
    fs_changed_at_unix: float | None
    fs_changed_at: int | None
    fs_created_at_unix: float | None
    fs_created_at: int | None
    fs_accessed_at_unix: float | None
    fs_accessed_at: int | None
    scanned_at_unix: float
    scanned_at: int


@dataclass
class ParsedFilename:
    """Parsed components of a filename."""

    full: str
    base: str
    extension: str | None
