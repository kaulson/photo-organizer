# MetadataExtractor Specification

## Overview

The MetadataExtractor reads embedded metadata from image and video files using exiftool, storing results in a dedicated `file_metadata` table. This component extracts date information, camera details, GPS coordinates, and other metadata that may be useful for file organization and deduplication.

### Relationship to Other Components

```
┌─────────────────────┐
│      Scanner        │  → Populates `files` table with filesystem info
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  PathDateExtractor  │  → Populates date_path_* columns from path patterns
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ MetadataExtractor   │  → Populates `file_metadata` table (this spec)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│      Planner        │  → Combines all signals to determine target paths
└─────────────────────┘
```

The MetadataExtractor operates independently of PathDateExtractor. It does not resolve dates — it only extracts and stores raw metadata. The Planner is responsible for combining path-based dates, metadata dates, and sibling inference to determine each file's final resolved date and target location.

---

## Dependencies

### exiftool (Required)

MetadataExtractor uses [exiftool](https://exiftool.org/) for metadata extraction. exiftool is the gold standard for reading metadata across image and video formats.

**Dependency check:** On instantiation, MetadataExtractor must verify exiftool is available by running `exiftool -ver`. If not found, raise an error immediately:

```
MetadataExtractorError: exiftool is required but not found.
Please install exiftool: https://exiftool.org/install.html
```

Do not attempt extraction or provide platform-specific installation instructions — just link to the official install page.

---

## Supported File Types

### Images

| Extension | Format | Notes |
|-----------|--------|-------|
| arw | Sony RAW | Full EXIF support |
| jpg | JPEG | Full EXIF support |
| jpeg | JPEG | Full EXIF support |
| nef | Nikon RAW | Full EXIF support |
| dng | Adobe DNG | Full EXIF support |
| tif | TIFF | Full EXIF support |
| tiff | TIFF | Full EXIF support |
| heic | HEIF (Apple) | Full EXIF support |
| cr2 | Canon RAW | Full EXIF support |
| srw | Samsung RAW | Full EXIF support |

### Videos

| Extension | Format | Notes |
|-----------|--------|-------|
| mp4 | MPEG-4 | QuickTime metadata |
| m4v | MPEG-4 Video | QuickTime metadata |
| mov | QuickTime | QuickTime metadata |
| mkv | Matroska | Matroska tags |
| avi | AVI | RIFF metadata |

### Explicitly Not Supported

| Extension | Reason |
|-----------|--------|
| png | No standard EXIF support; rarely contains useful metadata |
| psd | Photoshop files; metadata often reflects edit state, not capture |

---

## File Size Threshold

Files below a minimum size threshold are skipped without calling exiftool. This catches corrupted files, placeholder files, and truncated transfers that would otherwise waste processing time or produce misleading results.

### Threshold Value

```python
MIN_FILE_SIZE_BYTES = 10 * 1024  # 10 KB
```

**Rationale:**
- A typical JPEG thumbnail is ~10KB
- The smallest valid camera RAW file is ~1MB
- Files at 4KB are often filesystem placeholders or corrupted transfers
- 10KB catches corrupted files while allowing small but valid images

### Skip Behavior

Files below the threshold:
- Are **not** sent to exiftool
- Are recorded in `file_metadata` with `skip_reason` set (e.g., `file_too_small:4096_bytes`)
- Have all metadata columns set to NULL
- Are counted as "skipped" in statistics, separate from errors

This allows analysis of why files were excluded and supports future re-processing if thresholds change.

---

## Extraction Strategies

MetadataExtractor supports pluggable strategies that determine which files to process. Strategies are extensible — new ones can be added without modifying core extraction logic.

### Built-in Strategies

#### `full`

Process all files with supported extensions.

```sql
SELECT f.id FROM files f
WHERE f.extension IN ('arw', 'jpg', 'jpeg', 'nef', 'dng', 'tif', 'tiff',
                      'heic', 'cr2', 'srw', 'mp4', 'm4v', 'mov', 'mkv', 'avi')
  AND f.id NOT IN (SELECT file_id FROM file_metadata)
ORDER BY f.id
```

**Use case:** Initial full extraction, building complete metadata inventory.

#### `selective`

Process only files that have no path-based date.

```sql
SELECT f.id FROM files f
WHERE f.extension IN ('arw', 'jpg', 'jpeg', 'nef', 'dng', 'tif', 'tiff',
                      'heic', 'cr2', 'srw', 'mp4', 'm4v', 'mov', 'mkv', 'avi')
  AND f.date_path_folder IS NULL
  AND f.date_path_filename IS NULL
  AND f.id NOT IN (SELECT file_id FROM file_metadata)
ORDER BY f.id
```

**Use case:** Quick pass to fill gaps where path-based dates are unavailable.

### Extension Format

**Important:** Extensions in the `files` table are stored **without** the dot prefix (e.g., `jpg` not `.jpg`). Strategies must query accordingly.

### Future Strategy Ideas (Not Implemented)

- `selective_plus`: Selective, plus one random file per folder as sanity check
- `by_extension`: Process only specific extensions
- `by_directory`: Process only files in specific directory trees
- `missing_camera`: Process files where folder name suggests camera model but we haven't verified

### Strategy Interface

Strategies must implement:

```python
class ExtractionStrategy(Protocol):
    """Protocol for metadata extraction strategies."""

    name: str  # e.g., "full", "selective"

    def get_file_ids(
        self, conn: sqlite3.Connection, limit: int | None = None
    ) -> list[int]:
        """Return list of file IDs to process."""
```

---

## Path Resolution

The Scanner stores **relative paths** in the `files.source_path` column, with the root stored in `scan_sessions.source_root`. The MetadataExtractor must reconstruct absolute paths for exiftool:

```python
def _fetch_file_paths(self, file_ids: list[int]) -> list[dict]:
    """Fetch source paths and sizes for file IDs, returning absolute paths."""
    query = """
        SELECT f.id, f.source_path, f.size, s.source_root
        FROM files f
        JOIN scan_sessions s ON f.scan_session_id = s.id
        WHERE f.id IN (...)
    """
    # Construct absolute path
    absolute_path = f"{source_root}/{relative_path}"
```

This design allows the same database to be used even if the source drive is mounted at different locations between runs.

---

## Database Schema

### Table: `file_metadata`

```sql
CREATE TABLE IF NOT EXISTS file_metadata (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,

    -- Core dates (exiftool normalizes these across formats)
    -- DateTimeOriginal (EXIF) or CreateDate (QuickTime)
    date_original_unix REAL,
    date_original INTEGER,

    -- DateTimeDigitized (EXIF) or MediaCreateDate (QuickTime)
    date_digitized_unix REAL,
    date_digitized INTEGER,

    -- ModifyDate (EXIF and QuickTime)
    date_modify_unix REAL,
    date_modify INTEGER,

    -- Camera/device info
    make TEXT,                    -- e.g., "Sony", "Apple", "NIKON CORPORATION"
    model TEXT,                   -- e.g., "ILCE-7M3", "iPhone 12 Pro", "NIKON D5300"
    lens_model TEXT,              -- e.g., "FE 24-70mm F2.8 GM", null for phones/videos

    -- Dimensions
    image_width INTEGER,
    image_height INTEGER,
    orientation INTEGER,          -- EXIF orientation flag (1-8), null for videos

    -- Video-specific (null for images)
    duration_seconds REAL,
    video_frame_rate REAL,

    -- GPS (null if not present)
    gps_latitude REAL,            -- Decimal degrees, positive = North
    gps_longitude REAL,           -- Decimal degrees, positive = East
    gps_altitude REAL,            -- Meters above sea level

    -- Format info
    mime_type TEXT,               -- e.g., "image/jpeg", "video/mp4"
    metadata_families TEXT,       -- Comma-separated: "EXIF,XMP,QuickTime"

    -- Full dump (filtered, no binary data)
    metadata_json TEXT,

    -- Extraction tracking
    extracted_at_unix REAL NOT NULL,
    extracted_at INTEGER NOT NULL,
    extractor_version TEXT,       -- exiftool version string
    extraction_error TEXT,        -- null if success, error message if failed
    skip_reason TEXT              -- null if processed, reason if skipped (e.g., "file_too_small:4096_bytes")
);

-- Indexes
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
```

### Outcome Categories

Each file in `file_metadata` falls into exactly one category:

| Category | Condition | Meaning |
|----------|-----------|--------|
| **Success** | `extraction_error IS NULL AND skip_reason IS NULL` | Metadata extracted successfully |
| **Error** | `extraction_error IS NOT NULL` | exiftool failed (file corrupted, permission denied, etc.) |
| **Skipped** | `skip_reason IS NOT NULL` | File excluded before exiftool (too small, etc.) |

### Migration

For existing databases, add the `skip_reason` column:

```python
def migrate_add_skip_reason_column(conn: sqlite3.Connection) -> None:
    """Add skip_reason column to file_metadata table if it doesn't exist."""
    cursor = conn.execute("PRAGMA table_info(file_metadata)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if "skip_reason" not in existing_columns:
        conn.execute("ALTER TABLE file_metadata ADD COLUMN skip_reason TEXT")
        conn.commit()
```

---

## exiftool Invocation

### Command Structure

```bash
exiftool -json -struct -G0 -n -c "%.6f" <filepath>
```

**Flags explained:**

| Flag | Purpose |
|------|---------|
| `-json` | Output as JSON for easy parsing |
| `-struct` | Preserve structure for complex fields |
| `-G0` | Include family 0 group name (e.g., `EXIF:DateTimeOriginal`) |
| `-n` | Output numeric values (not human-readable) |
| `-c "%.6f"` | GPS coordinates as decimal degrees with 6 decimal places |

### Batch Processing

For performance, process files in batches:

```bash
exiftool -json -struct -G0 -n -c "%.6f" file1.jpg file2.arw file3.mp4
```

exiftool returns a JSON array with one object per file. Match results to files by the `SourceFile` field.

**Recommended batch size:** 100 files (balance between subprocess overhead and memory usage).

### Handling Failures

If exiftool fails on a specific file:
- Store `extraction_error` with the error message
- Leave all other columns as NULL
- Continue processing remaining files

If exiftool crashes on a batch:
- Fall back to processing files individually
- Log which file caused the crash

---

## Field Mapping

### Date Fields

exiftool normalizes dates but reports them under different group names depending on format:

| Column | EXIF Source | QuickTime Source | Fallback |
|--------|-------------|------------------|----------|
| `date_original` | `EXIF:DateTimeOriginal` | `QuickTime:CreateDate` | `XMP:DateTimeOriginal` |
| `date_digitized` | `EXIF:DateTimeDigitized` | `QuickTime:MediaCreateDate` | `XMP:CreateDate` |
| `date_modify` | `EXIF:ModifyDate` | `QuickTime:ModifyDate` | `XMP:ModifyDate` |

**Date parsing:**

exiftool with `-n` flag returns dates as strings like `"2023:05:14 13:45:30"`. Parse with:

```python
datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
```

Handle timezone-aware strings (some QuickTime dates include timezone):
```python
# "2023:05:14 13:45:30+02:00" or "2023:05:14 13:45:30Z"
```

Store as both Unix timestamp (`REAL`) and integer YYYYMMDD for consistency with path-based dates.

### Camera Fields

| Column | Source Fields (priority order) |
|--------|-------------------------------|
| `make` | `EXIF:Make`, `QuickTime:Make`, `XMP:Make` |
| `model` | `EXIF:Model`, `QuickTime:Model`, `XMP:Model` |
| `lens_model` | `EXIF:LensModel`, `EXIF:Lens`, `XMP:Lens` |

### Dimension Fields

| Column | Source Fields |
|--------|---------------|
| `image_width` | `EXIF:ImageWidth`, `EXIF:ExifImageWidth`, `QuickTime:ImageWidth` |
| `image_height` | `EXIF:ImageHeight`, `EXIF:ExifImageHeight`, `QuickTime:ImageHeight` |
| `orientation` | `EXIF:Orientation` (integer 1-8) |

### Video Fields

| Column | Source Fields |
|--------|---------------|
| `duration_seconds` | `QuickTime:Duration`, `Matroska:Duration` |
| `video_frame_rate` | `QuickTime:VideoFrameRate`, `Matroska:FrameRate` |

### GPS Fields

| Column | Source | Notes |
|--------|--------|-------|
| `gps_latitude` | `EXIF:GPSLatitude` | With `-n -c "%.6f"`, already decimal degrees |
| `gps_longitude` | `EXIF:GPSLongitude` | Already decimal degrees |
| `gps_altitude` | `EXIF:GPSAltitude` | Meters |

**Sign convention:** exiftool with `-n` returns signed values (negative for South/West).

### Metadata Families

Extract unique group names (family 0) from all returned fields:

```python
families = set()
for key in exif_data.keys():
    if ":" in key:
        families.add(key.split(":")[0])
metadata_families = ",".join(sorted(families))
# e.g., "EXIF,File,ICC_Profile,JFIF,XMP"
```

---

## JSON Dump Filtering

The `metadata_json` column stores all extracted metadata except binary data.

### Fields to Exclude

Exclude fields containing binary/thumbnail data:

```python
EXCLUDED_FIELDS = {
    "EXIF:ThumbnailImage",
    "EXIF:ThumbnailTIFF",
    "EXIF:PreviewImage",
    "EXIF:JpgFromRaw",
    "EXIF:OtherImage",
    "ICC_Profile:ProfileCMMType",  # Can be large
    "File:Directory",              # Redundant (we have source_path)
    "File:FileName",               # Redundant
    "SourceFile",                  # Redundant
}

# Also exclude any field where the value starts with "base64:" or "(Binary data"
```

### JSON Structure

Store as a flat JSON object with group-prefixed keys:

```json
{
    "EXIF:DateTimeOriginal": "2023:05:14 13:45:30",
    "EXIF:Make": "Sony",
    "EXIF:Model": "ILCE-7M3",
    "EXIF:ISO": 400,
    "EXIF:FNumber": 2.8,
    "EXIF:ExposureTime": 0.004,
    "XMP:Rating": 3,
    "QuickTime:Duration": 45.2
}
```

---

## Progress Reporting

Use the same progress reporting pattern as Scanner:

```
Starting metadata extraction (strategy: full)
exiftool version: 12.76
Files to process: 325,412

[1000/325412] Processing... (3.2 files/sec)
[2000/325412] Processing... (3.1 files/sec)
...
Extraction complete: 325,000 succeeded, 412 skipped, 0 errors
```

### Statistics

Track and report:
- `total_files` — Total files processed (including skipped)
- `files_extracted` — Successfully extracted metadata
- `files_skipped` — Skipped (e.g., below size threshold)
- `files_failed` — exiftool errors
- `files_with_date_original` — Have DateTimeOriginal
- `files_with_gps` — Have GPS coordinates

### Resumability

Extraction is inherently resumable — the strategy queries exclude files already in `file_metadata`:

```sql
AND id NOT IN (SELECT file_id FROM file_metadata)
```

If interrupted, simply run again with the same strategy.

---

## CLI Interface

```bash
# Full extraction (all supported files)
uv run photosort extract-metadata --strategy full

# Selective extraction (dateless files only)
uv run photosort extract-metadata --strategy selective

# Limit for testing
uv run photosort extract-metadata --strategy full --limit 100

# Show extraction stats
uv run photosort extract-metadata --stats
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--strategy` | `selective` | Extraction strategy: `full`, `selective` |
| `--batch-size` | `100` | Files per exiftool invocation |
| `--limit` | None | Maximum files to process (for testing) |
| `--stats` | False | Show extraction statistics and exit |

### Output Examples

**Extraction run:**
```
Starting metadata extraction (strategy: full)
exiftool version: 13.44

Metadata Extraction Complete:
  Total files processed: 11,000
  Successfully extracted: 5,648
  Skipped (too small): 5,352
  With original date: 5,183
  With GPS: 44
  Errors: 0
```

**Statistics:**
```
Metadata Extraction Statistics:
  Total extracted: 11,000
  Successful: 5,648
  Skipped: 5,352
  Errors: 0
  With date: 5,183
  With GPS: 44
```

---

## Error Handling

### exiftool Not Found

```python
class MetadataExtractorError(Exception):
    """Base exception for metadata extraction errors."""
    pass

class ExiftoolNotFoundError(MetadataExtractorError):
    """Raised when exiftool is not installed."""
    pass

# On instantiation:
def __init__(self, ...):
    if not self._check_exiftool():
        raise ExiftoolNotFoundError(
            "exiftool is required but not found.\n"
            "Please install exiftool: https://exiftool.org/install.html"
        )
```

### Per-File Outcomes

| Outcome | Column | Example Value |
|---------|--------|---------------|
| Success | (all null) | — |
| Skipped | `skip_reason` | `file_too_small:4096_bytes` |
| Error | `extraction_error` | `File not found`, `Permission denied` |

Common errors stored in `extraction_error`:

| Error | Meaning |
|-------|---------|
| `"File not found"` | File was deleted after scan |
| `"Permission denied"` | Cannot read file |
| `"Unknown file type"` | exiftool doesn't recognize format |
| `"Corrupted metadata"` | EXIF data is malformed |
| `"No exiftool result"` | exiftool returned no data for this file |

---

## Analysis Queries

After extraction, useful queries for understanding the data:

**Extraction coverage:**
```sql
SELECT
    f.extension,
    COUNT(*) as total_files,
    COUNT(m.id) as extracted,
    SUM(CASE WHEN m.skip_reason IS NOT NULL THEN 1 ELSE 0 END) as skipped,
    SUM(CASE WHEN m.extraction_error IS NOT NULL THEN 1 ELSE 0 END) as errors,
    ROUND(100.0 * COUNT(m.id) / COUNT(*), 1) as coverage_pct
FROM files f
LEFT JOIN file_metadata m ON f.id = m.file_id
WHERE f.extension IN ('arw', 'jpg', 'jpeg', 'nef', 'dng', 'tif', 'tiff',
                      'heic', 'cr2', 'srw', 'mp4', 'm4v', 'mov', 'mkv', 'avi')
GROUP BY f.extension
ORDER BY total_files DESC;
```

**Skip reason distribution:**
```sql
SELECT skip_reason, COUNT(*) as cnt
FROM file_metadata
WHERE skip_reason IS NOT NULL
GROUP BY skip_reason
ORDER BY cnt DESC;
```

**Files with metadata dates but no path dates:**
```sql
SELECT f.source_path, f.extension, m.date_original, m.make, m.model
FROM files f
JOIN file_metadata m ON f.id = m.file_id
WHERE f.date_path_folder IS NULL
  AND f.date_path_filename IS NULL
  AND m.date_original IS NOT NULL
ORDER BY RANDOM()
LIMIT 50;
```

**Path date vs metadata date comparison:**
```sql
SELECT
    f.source_path,
    COALESCE(f.date_path_folder, f.date_path_filename) as path_date,
    m.date_original as exif_date,
    ABS(COALESCE(f.date_path_folder, f.date_path_filename) - m.date_original) as diff_days
FROM files f
JOIN file_metadata m ON f.id = m.file_id
WHERE COALESCE(f.date_path_folder, f.date_path_filename) IS NOT NULL
  AND m.date_original IS NOT NULL
  AND ABS(COALESCE(f.date_path_folder, f.date_path_filename) - m.date_original) > 1
ORDER BY diff_days DESC
LIMIT 50;
```

**Camera model distribution:**
```sql
SELECT make, model, COUNT(*) as file_count
FROM file_metadata
WHERE make IS NOT NULL AND skip_reason IS NULL
GROUP BY make, model
ORDER BY file_count DESC
LIMIT 30;
```

**Files with GPS data:**
```sql
SELECT f.source_path, m.gps_latitude, m.gps_longitude, m.date_original
FROM files f
JOIN file_metadata m ON f.id = m.file_id
WHERE m.gps_latitude IS NOT NULL
ORDER BY m.date_original DESC
LIMIT 50;
```

**Extraction errors:**
```sql
SELECT f.source_path, f.extension, m.extraction_error
FROM files f
JOIN file_metadata m ON f.id = m.file_id
WHERE m.extraction_error IS NOT NULL
ORDER BY f.extension, m.extraction_error;
```

---

## Future Work (Out of Scope)

The following are explicitly deferred:

1. **Sibling inference** — inferring dates for sidecars from neighboring images
2. **GPS reverse geocoding** — converting coordinates to location names
3. **Duplicate detection via metadata** — comparing EXIF timestamps for potential duplicates
4. **Camera serial number extraction** — for tracking individual camera bodies
5. **Processing PNG/PSD** — could be added if needed, but likely low value
6. **Configurable size threshold** — currently hardcoded at 10KB

These may be added as additional strategies or separate components.

---

## Revision History

### Changes from Initial Specification

For readers familiar with the original specification, the following changes were made during implementation:

1. **File Size Threshold** — Added `MIN_FILE_SIZE_BYTES` (10KB) to skip corrupted/placeholder files before calling exiftool. This was discovered necessary when real-world data contained thousands of 4KB placeholder files from incomplete transfers.

2. **Skip Reason Column** — Added `skip_reason` column to `file_metadata` to distinguish between files that were skipped intentionally (e.g., too small) vs. files that failed during extraction. This provides better visibility into why files weren't processed.

3. **Path Resolution** — Clarified that the Scanner stores relative paths in `files.source_path` with the root in `scan_sessions.source_root`. The extractor must join these tables to construct absolute paths for exiftool. This supports mounting drives at different locations between runs.

4. **Extension Format** — Explicitly documented that extensions are stored without the dot prefix (`jpg` not `.jpg`). The original spec's SQL examples used the dot prefix which would match zero files.

5. **Statistics Enhancement** — Added `files_skipped` counter to `MetadataExtractorStats` and updated CLI output to show skipped files separately from errors.

6. **Strategy Interface** — Simplified to remove `scan_session_id` parameter (strategies query all sessions) and added optional `limit` parameter.
