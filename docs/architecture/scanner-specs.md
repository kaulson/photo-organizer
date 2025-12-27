# Scanner Component Specification

## Overview

The Scanner is the first phase of the photo organization pipeline. Its sole responsibility is to walk a source filesystem and collect file metadata into a SQLite database. It does not extract EXIF data, classify files, or make organizational decisions—those are separate phases.

### Design Philosophy

- **Maximalist data collection**: Gather all available metadata now, make tradeoffs later based on real data
- **Single responsibility**: Scan filesystem, store metadata, nothing more
- **Resumable by design**: Can be interrupted and resumed without data loss
- **Speed-optimized**: Use fastest available APIs for filesystem traversal

---

## Functional Requirements

### Core Behavior

1. **Walk the source filesystem** recursively, visiting every file
2. **Collect filesystem metadata** for each file (size, timestamps, path components)
3. **Store results** in SQLite database
4. **Track progress** at directory level for resumability
5. **Report progress** to user during scan

### Filesystem Handling

| Aspect | Behavior |
|--------|----------|
| Symlinks | Do not follow (skip symlinks entirely) |
| Hidden files/folders | Include all (`.dotfiles`, `.folders`) |
| Permission errors | Log warning, continue scanning |
| Corrupted entries | Log error, continue scanning |
| Empty directories | Record in completed_directories, no file entries |

### Resumability

- Track completed directories in database
- On interrupt: current directory may be partially scanned
- On resume: re-scan the incomplete directory from scratch, continue with remaining
- Commit progress in batches (per-directory)

### Scan Session Management

- Each scan targets a single source root path
- A new scan of the same source_root **overwrites** the previous scan data
- Before starting: delete existing session and all related data for that source_root
- Drive UUID is **required** (fail if cannot determine)

---

## Technical Implementation

### Filesystem Traversal Strategy

Use hybrid approach for optimal performance:

```
os.scandir()  - For directory walking (fastest, caches stat results)
pathlib.Path  - For path manipulation (clean API for extensions, names)
```

**Rationale**: `os.scandir()` returns `DirEntry` objects that cache `stat()` results from the kernel's directory read. This avoids separate syscalls for `is_file()` and `stat()`. Path objects are only created when we need to parse path components.

### Directory Processing Order

Directories are processed in **alphabetical order** within each level. This provides:
- Predictable, reproducible scan order
- Easier debugging ("it stopped at /photos/2019/m...")
- Consistent behavior across runs

### Batch Commits

- Commit transaction after each **completed directory**
- Update `completed_directories` table atomically with file inserts
- On interrupt: lose at most one directory's worth of work

### Progress Reporting

Print status every N files (configurable, default 1000):
```
[12,847 files] Scanning: /photos/2019/march/beach_trip/
[13,892 files] Scanning: /photos/2019/march/birthday/
```

On completion:
```
Scan complete: 2,847,293 files in 487,291 directories (3h 24m 17s)
```

---

## Data Model

### Schema

```sql
-- Scan session tracking
CREATE TABLE scan_sessions (
    id INTEGER PRIMARY KEY,
    
    -- Source identification
    source_root TEXT NOT NULL,           -- Absolute path to scan root
    source_drive_uuid TEXT NOT NULL,     -- UUID of source drive/partition
    
    -- Timing
    started_at_unix REAL NOT NULL,       -- Unix timestamp (fractional seconds)
    started_at INTEGER NOT NULL,         -- Unix timestamp (second precision)
    completed_at_unix REAL,
    completed_at INTEGER,
    
    -- Status
    status TEXT NOT NULL,                -- 'running', 'completed', 'failed'
    error_message TEXT,                  -- If status is 'failed'
    
    -- Statistics
    files_scanned INTEGER DEFAULT 0,
    directories_scanned INTEGER DEFAULT 0,
    total_bytes INTEGER DEFAULT 0,
    
    UNIQUE(source_root)                  -- Only one session per source_root
);

-- Directory completion tracking (for resumability)
CREATE TABLE completed_directories (
    id INTEGER PRIMARY KEY,
    scan_session_id INTEGER NOT NULL REFERENCES scan_sessions(id) ON DELETE CASCADE,
    
    -- Path (relative to source_root, empty string for root itself)
    directory_path TEXT NOT NULL,
    
    -- Statistics
    file_count INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    
    -- Timing
    completed_at_unix REAL NOT NULL,
    completed_at INTEGER NOT NULL,
    
    UNIQUE(scan_session_id, directory_path)
);

-- File inventory
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    scan_session_id INTEGER NOT NULL REFERENCES scan_sessions(id) ON DELETE CASCADE,
    
    -- Path components (all paths relative to source_root)
    source_path TEXT NOT NULL,           -- Full relative path: "2019/march/IMG_001.CR2"
    directory_path TEXT NOT NULL,        -- Parent directory: "2019/march"
    
    -- Filename components
    filename_full TEXT NOT NULL,         -- Full filename: "archive.tar.gz"
    filename_base TEXT NOT NULL,         -- Base name: "archive" (before first dot) or "archive.tar" (before last dot)?
    extension TEXT,                      -- Extension: "gz" (lowercase, no dot, NULL if none)
    
    -- Size
    size INTEGER NOT NULL,               -- File size in bytes
    
    -- Filesystem timestamps (dual precision)
    fs_modified_at_unix REAL,            -- st_mtime as fractional seconds
    fs_modified_at INTEGER,              -- st_mtime as whole seconds
    fs_changed_at_unix REAL,             -- st_ctime (inode change on Linux)
    fs_changed_at INTEGER,
    fs_created_at_unix REAL,             -- st_birthtime (NULL if unavailable)
    fs_created_at INTEGER,
    fs_accessed_at_unix REAL,            -- st_atime
    fs_accessed_at INTEGER,
    
    -- Hashes (populated by deduplication phase, NULL initially)
    hash_quick_start TEXT,               -- Hash of first 64KB
    hash_quick_end TEXT,                 -- Hash of last 64KB
    hash_full TEXT,                      -- Full file SHA-256
    
    -- EXIF dates (populated by metadata extraction phase, NULL initially)
    date_exif_original_unix REAL,
    date_exif_original INTEGER,
    date_exif_create_unix REAL,
    date_exif_create INTEGER,
    date_exif_modify_unix REAL,
    date_exif_modify INTEGER,
    
    -- Path-derived date (populated by metadata extraction phase)
    date_path_derived_unix REAL,
    date_path_derived INTEGER,
    
    -- Classification (populated by classification phase, NULL initially)
    file_type TEXT,                      -- camera_raw, phone_photo, etc.
    exif_make TEXT,
    exif_model TEXT,
    
    -- Flexible metadata storage
    metadata_json TEXT,                  -- JSON blob for additional extracted data
    
    -- Processing timestamps
    scanned_at_unix REAL NOT NULL,
    scanned_at INTEGER NOT NULL,
    metadata_extracted_at_unix REAL,
    metadata_extracted_at INTEGER,
    classified_at_unix REAL,
    classified_at INTEGER,
    
    UNIQUE(scan_session_id, source_path)
);

-- Indexes for scanner operations
CREATE INDEX idx_files_session ON files(scan_session_id);
CREATE INDEX idx_files_directory ON files(scan_session_id, directory_path);
CREATE INDEX idx_completed_dirs_session ON completed_directories(scan_session_id);

-- Indexes for later phases
CREATE INDEX idx_files_size ON files(size);
CREATE INDEX idx_files_extension ON files(extension) WHERE extension IS NOT NULL;
CREATE INDEX idx_files_hash_quick ON files(hash_quick_start) WHERE hash_quick_start IS NOT NULL;
CREATE INDEX idx_files_hash_full ON files(hash_full) WHERE hash_full IS NOT NULL;
```

### Filename Parsing Rules

Given a file path, extract components as follows:

| Full Filename | filename_full | filename_base | extension |
|---------------|---------------|---------------|-----------|
| `photo.JPG` | `photo.JPG` | `photo` | `jpg` |
| `archive.tar.gz` | `archive.tar.gz` | `archive.tar` | `gz` |
| `README` | `README` | `README` | `NULL` |
| `.gitignore` | `.gitignore` | `.gitignore` | `NULL` |
| `.config.yaml` | `.config.yaml` | `.config` | `yaml` |
| `file.` | `file.` | `file` | `NULL` |
| `photo.JPG.xmp` | `photo.JPG.xmp` | `photo.JPG` | `xmp` |

**Logic**:
- `filename_full`: Entire filename as-is
- `extension`: Text after the **last** dot, lowercased. `NULL` if no dot, or dot is first character with nothing after, or dot is last character
- `filename_base`: Everything before the last dot (or full name if no valid extension)

**Implementation**:
```python
from pathlib import Path

def parse_filename(filename: str) -> tuple[str, str, str | None]:
    """Returns (filename_full, filename_base, extension)"""
    filename_full = filename
    
    path = Path(filename)
    suffix = path.suffix  # Includes the dot, e.g., ".jpg"
    
    if suffix and suffix != '.':
        extension = suffix[1:].lower()  # Remove dot, lowercase
        filename_base = filename[:-len(suffix)]
        # Handle edge case: ".gitignore" has suffix="" in pathlib
    else:
        extension = None
        filename_base = filename
    
    # Edge case: dotfiles like ".gitignore" 
    # pathlib treats these as having no suffix, stem is ".gitignore"
    # This is correct for our purposes
    
    # Edge case: "file." - suffix is ".", we treat as no extension
    if suffix == '.':
        extension = None
        filename_base = filename[:-1]
    
    return filename_full, filename_base, extension
```

### Path Storage

All paths are stored **relative to source_root**:

| source_root | Absolute Path | Stored source_path |
|-------------|---------------|-------------------|
| `/mnt/drive1` | `/mnt/drive1/photos/2019/img.jpg` | `photos/2019/img.jpg` |
| `/mnt/drive1` | `/mnt/drive1/img.jpg` | `img.jpg` |
| `/mnt/drive1/photos` | `/mnt/drive1/photos/2019/img.jpg` | `2019/img.jpg` |

The **root directory itself** is represented as empty string `""` in `directory_path`.

### Timestamp Storage

Each timestamp is stored in two columns for flexibility:

| Column Suffix | Type | Content | Example |
|---------------|------|---------|---------|
| `_unix` | REAL | Fractional seconds since epoch | `1703683200.123456` |
| (no suffix) | INTEGER | Whole seconds since epoch | `1703683200` |

**Rationale**: REAL provides sub-second precision for forensic purposes. INTEGER is easier for date calculations and comparisons in SQL. Both are derived from the same source value.

---

## Drive UUID Detection

### Requirements

- UUID is **required** for scan to proceed
- Fail with clear error if UUID cannot be determined
- Future: make optional with warning

### Implementation

Given a mount point, determine the drive UUID:

```python
import subprocess
from pathlib import Path

def get_drive_uuid(mount_point: str) -> str:
    """
    Get the UUID of the drive mounted at the given path.
    Raises RuntimeError if UUID cannot be determined.
    """
    mount_point = str(Path(mount_point).resolve())
    
    # Use findmnt to get the device for this mount point
    result = subprocess.run(
        ['findmnt', '-n', '-o', 'SOURCE', mount_point],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"Could not find mount point: {mount_point}")
    
    device = result.stdout.strip()
    
    # Handle device mapper, LVM, etc.
    # The device might be like /dev/sda1, /dev/mapper/..., etc.
    
    # Use lsblk to get UUID
    result = subprocess.run(
        ['lsblk', '-n', '-o', 'UUID', device],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"Could not get UUID for device: {device}")
    
    uuid = result.stdout.strip()
    
    if not uuid:
        raise RuntimeError(
            f"No UUID found for {mount_point} (device: {device}). "
            "This may be a network share or virtual filesystem."
        )
    
    return uuid
```

---

## Error Handling

### Error Categories

| Error Type | Behavior | Logging |
|------------|----------|---------|
| Permission denied on file | Skip file, continue | WARNING with path |
| Permission denied on directory | Skip directory and contents, continue | WARNING with path |
| File disappeared during scan | Skip file, continue | WARNING with path |
| Corrupted directory entry | Skip entry, continue | ERROR with path and exception |
| Disk I/O error | Retry once, then skip and continue | ERROR with details |
| Database error | Fail scan, preserve progress | CRITICAL, raise exception |
| Cannot determine UUID | Fail scan before starting | CRITICAL, raise exception |

### Error Logging Format

```
[WARNING] Permission denied: /photos/private/secret.jpg
[WARNING] Directory inaccessible: /photos/corrupted_folder/
[ERROR] Failed to stat file: /photos/weird.jpg - OSError: [Errno 5] Input/output error
```

---

## Resume Logic

### Detecting Incomplete Scan

On startup, check for existing session:

```sql
SELECT * FROM scan_sessions 
WHERE source_root = ? AND status = 'running'
```

If found: previous scan was interrupted.

### Resume Process

1. Query completed directories:
   ```sql
   SELECT directory_path FROM completed_directories 
   WHERE scan_session_id = ?
   ```

2. Build set of completed directory paths

3. Walk filesystem in alphabetical order

4. For each directory:
   - If in completed set: skip entirely
   - If not in completed set: scan fully (delete any existing files for this directory first)

### Handling Partial Directory

If a directory was partially scanned (files exist but not in completed_directories):

```sql
-- Delete partial results before re-scanning
DELETE FROM files 
WHERE scan_session_id = ? AND directory_path = ?
```

Then scan the directory fresh.

---

## Configuration

### Scanner-Specific Settings

```yaml
scanner:
  # Progress reporting
  progress_interval: 1000          # Print status every N files
  
  # Batch size (commits per directory, but also interim stats update)
  stats_update_interval: 100       # Update session stats every N files
  
  # Error handling
  retry_io_errors: true            # Retry once on I/O errors
  max_path_length: 4096            # Skip files with paths longer than this
  
  # Future: exclusion patterns (not implemented in v1)
  # exclude_patterns:
  #   - ".Trash-*"
  #   - ".thumbnails"
```

---

## CLI Interface

### Commands

```bash
# Start a new scan (overwrites any existing scan of same source)
photosort scan /mnt/source_drive

# Resume an interrupted scan
photosort scan --resume /mnt/source_drive

# Show scan status
photosort scan --status

# Scan with custom progress interval
photosort scan --progress-interval 5000 /mnt/source_drive
```

### Output Examples

**Starting fresh scan:**
```
$ photosort scan /mnt/photos

Starting scan of /mnt/photos
Drive UUID: 1234abcd-5678-efgh-ijkl-9999mmmm0000
Previous scan data will be overwritten.

[1,000 files] Scanning: 2018/january/new_years/
[2,000 files] Scanning: 2018/february/ski_trip/
[3,000 files] Scanning: 2018/march/
^C
Scan interrupted. Progress saved. Run with --resume to continue.
Scanned: 3,847 files in 23 directories
```

**Resuming:**
```
$ photosort scan --resume /mnt/photos

Resuming scan of /mnt/photos
Previous progress: 3,823 files in 22 directories
Skipping 22 completed directories...

[3,824 files] Scanning: 2018/march/
[4,500 files] Scanning: 2018/april/easter/
...
Scan complete: 2,847,293 files in 487,291 directories
Total size: 5.82 TB
Duration: 3h 24m 17s
```

**Status check:**
```
$ photosort scan --status

Scan Sessions:
┌─────────────────────┬──────────────┬───────────┬─────────────┬──────────┐
│ Source              │ Status       │ Files     │ Size        │ Started  │
├─────────────────────┼──────────────┼───────────┼─────────────┼──────────┤
│ /mnt/photos         │ interrupted  │ 3,847     │ 12.4 GB     │ 2h ago   │
│ /mnt/backup_drive   │ completed    │ 1,203,847 │ 2.1 TB      │ yesterday│
└─────────────────────┴──────────────┴───────────┴─────────────┴──────────┘
```

---

## Module Structure

```
photosort/
├── __init__.py
├── cli.py                    # Click-based CLI entry points
├── config.py                 # Configuration loading
├── database/
│   ├── __init__.py
│   ├── connection.py         # Database connection management
│   ├── schema.py             # Schema creation and migrations
│   └── models.py             # Data classes for rows (optional)
└── scanner/
    ├── __init__.py
    ├── scanner.py            # Main Scanner class
    ├── filesystem.py         # Filesystem walking utilities
    ├── uuid.py               # Drive UUID detection
    └── progress.py           # Progress reporting
```

---

## Testing Strategy

### Unit Tests

| Component | Test Cases |
|-----------|------------|
| Filename parsing | All edge cases from table above |
| Path relativization | Various source_root configurations |
| UUID detection | Mock subprocess calls |
| Timestamp extraction | Files with/without birthtime |

### Integration Tests

| Scenario | Verification |
|----------|--------------|
| Scan empty directory | Session created, 0 files, 1 directory |
| Scan with nested structure | Correct path components stored |
| Scan with permission errors | Errors logged, scan completes |
| Interrupt and resume | No duplicate files, no missing files |
| Rescan same source | Old data deleted, fresh scan stored |

### Test Fixtures

Create synthetic test directory with:
- Normal files with various extensions
- Files without extensions
- Dotfiles (`.gitignore`, `.config`)
- Nested directories (5+ levels)
- Unicode filenames
- Very long filenames/paths
- Empty directories
- Unreadable files (chmod 000)
- Symlinks (should be skipped)

---

## Performance Considerations

### Expected Performance

Based on typical hardware:
- SSD source: 10,000-50,000 files/second (metadata only)
- HDD source: 1,000-5,000 files/second (seek-bound)
- Network share: 100-1,000 files/second (latency-bound)

For 10M files on HDD: expect 30 minutes to 3 hours.

### Bottlenecks

1. **Directory listing**: `os.scandir()` is optimal
2. **Database inserts**: Batched per-directory, should not be bottleneck
3. **Stat calls**: Cached by DirEntry on most filesystems
4. **UUID lookup**: Once per scan, negligible

### Memory Usage

- Keep minimal state in memory
- Process one directory at a time
- Completed directories tracked in DB, not memory
- Expected memory: <100MB regardless of file count

---

## Open Questions for Implementation

1. **filename_base definition**: Should `archive.tar.gz` have base `archive.tar` (before last dot) or `archive` (before first dot)? Document specifies last dot—confirm this is desired.

2. **Empty directory handling**: Should empty directories be recorded in `completed_directories`? Specified yes, with file_count=0.

3. **Root directory files**: Files directly in source_root have `directory_path = ""`. Confirm this representation.

4. **Symlink to file vs symlink to directory**: Both skipped, or just symlink-to-directory? Specified: skip all symlinks.

5. **Maximum path handling**: What if a path exceeds database limits? Skip with warning, or truncate? Specified: skip with warning.