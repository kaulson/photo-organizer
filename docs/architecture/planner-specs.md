# Planner Specification

## Overview

The Planner analyzes scanned files and determines where each file should be placed in the target directory structure. It operates at the **folder level** — resolving a date for each folder, then deriving target paths for all files within.

The Planner does not move or copy files. It produces a plan (stored in database tables) that a later component (SyncEngine) will execute.

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
│ MetadataExtractor   │  → Populates `file_metadata` table (EXIF, etc.)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│      Planner        │  → Populates `folder_plan` and `file_plan` tables (this spec)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│     SyncEngine      │  → Executes the plan, copies files (future spec)
└─────────────────────┘
```

---

## Core Concepts

### Folder-Centric Resolution

The Planner thinks in terms of **folders**, not individual files. This approach:

- Keeps related files together (RAW + sidecar + edits)
- Handles non-image files naturally (they inherit the folder's date)
- Simplifies conflict resolution
- Preserves intentional groupings

### Per-File Date Resolution

Before folder analysis, each file is assigned a **resolved date** using a priority hierarchy:

1. **Path date** (`date_path_folder` or `date_path_filename`) — highest priority
2. **EXIF date** (`file_metadata.date_original`) — if no path date
3. **Filesystem modified date** (`files.fs_modified_at`) — fallback

This gives every file (that has any date signal) a single resolved date for analysis.

### Target Structure

The target directory structure is:

```
yyyy/yyyy_mm/yyyymmdd[-annotation]/
```

Examples:
- `2023/2023_10/20231015/`
- `2023/2023_10/20231015-sunset/`
- `2023/2023_10/20231015-peter/`

### Buckets

Files that can't be placed in the standard date structure go into buckets:

| Bucket | Path | When used |
|--------|------|-----------|
| `_mixed_dates` | `_mixed_dates/<original-full-path>/` | Date spread ≥ 3 months, or low coverage |
| `_non_media` | `_non_media/<original-full-path>/` | Folders with no image files |

---

## File Date Resolution

### Priority Hierarchy

For each file, determine its resolved date:

```python
def resolve_file_date(file: FileRecord, metadata: FileMetadata | None) -> int | None:
    """
    Returns YYYYMMDD integer or None.
    Priority: path_date > exif_date > fs_modified_at
    """
    # 1. Path-derived date (highest priority)
    if file.date_path_folder:
        return file.date_path_folder
    if file.date_path_filename:
        return file.date_path_filename

    # 2. EXIF/metadata date
    if metadata and metadata.date_original:
        return metadata.date_original

    # 3. Filesystem modified date (fallback)
    if file.fs_modified_at:
        return unix_to_yyyymmdd(file.fs_modified_at)

    return None
```

**Note on fs_modified_at:** This fallback is used without sanity checks against other dates. A file's modified date may be years different from folder dates if it was edited later (e.g., developing a RAW file years after capture). This is acceptable because:
- Files with EXIF data use EXIF (higher priority)
- The folder-level statistical analysis smooths out outliers
- Edited files are still related to the original shoot

### Date Source Tracking

Track which source provided the date for debugging:

| Source | Meaning |
|--------|---------|
| `path_folder` | From `date_path_folder` column |
| `path_filename` | From `date_path_filename` column |
| `exif` | From `file_metadata.date_original` |
| `fs_modified` | From `files.fs_modified_at` |
| `null` | No date available |

---

## Folder Resolution Algorithm

### Step 1: Identify All Folders

Extract unique `directory_path` values from the `files` table.

### Step 2: Classify Folder Contents

For each folder, compute:

```sql
-- Folder statistics
total_file_count        -- All files in folder
image_file_count        -- Files with image extensions
images_with_date_count  -- Images that have a resolved date
date_coverage_pct       -- images_with_date_count / image_file_count
```

**Image extensions** (for classification purposes):
```python
IMAGE_EXTENSIONS = {
    'arw', 'jpg', 'jpeg', 'nef', 'dng', 'tif', 'tiff',
    'heic', 'cr2', 'srw', 'png', 'psd', 'bmp', 'gif'
}
```

### Step 3: Check for Path-Based Date

If any file in the folder has `date_path_folder` set, the folder has a path-derived date.

```python
def get_folder_path_date(folder_files: list[FileRecord]) -> tuple[int | None, str | None]:
    """Returns (date, source_folder_name) if folder has path-derived date."""
    for f in folder_files:
        if f.date_path_folder:
            return f.date_path_folder, f.date_path_folder_source
    return None, None
```

### Step 4: Statistical Analysis (if no path date)

If the folder has no path-derived date, analyze the dated images:

```python
@dataclass
class FolderDateAnalysis:
    total_files: int
    image_files: int
    images_with_date: int
    date_coverage_pct: float          # images_with_date / image_files

    prevalent_date: int | None        # Most common date (YYYYMMDD)
    prevalent_date_count: int
    prevalent_date_pct: float         # prevalent_count / images_with_date

    min_date: int | None
    max_date: int | None
    date_span_months: int             # Calendar months between min and max

    unique_date_count: int            # How many distinct dates
```

### Step 5: Apply Resolution Rules

```python
def resolve_folder(
    analysis: FolderDateAnalysis,
    config: PlannerConfig,
) -> FolderResolution:

    # No images at all → non_media bucket
    if analysis.image_files == 0:
        return FolderResolution(
            bucket='non_media',
            resolved_date=None,
            source='no_images',
        )

    # Low date coverage → mixed_dates bucket
    if analysis.date_coverage_pct < config.min_coverage_threshold:
        return FolderResolution(
            bucket='mixed_dates',
            resolved_date=None,
            source='low_coverage',
        )

    # Wide date spread → mixed_dates bucket
    if analysis.date_span_months >= config.max_date_span_months:
        return FolderResolution(
            bucket='mixed_dates',
            resolved_date=None,
            source='wide_spread',
        )

    # High prevalence → use prevalent date
    if analysis.prevalent_date_pct >= config.min_prevalence_threshold:
        return FolderResolution(
            bucket=None,
            resolved_date=analysis.prevalent_date,
            source='metadata_prevalent',
        )

    # 100% agreement (all dated images have same date)
    if analysis.unique_date_count == 1:
        return FolderResolution(
            bucket=None,
            resolved_date=analysis.prevalent_date,
            source='metadata_unanimous',
        )

    # Fallback: mixed_dates
    return FolderResolution(
        bucket='mixed_dates',
        resolved_date=None,
        source='no_consensus',
    )
```

### Step 6: Handle Inheritance

For nested folders, apply inheritance rules:

```python
def resolve_with_inheritance(
    folder: str,
    parent_resolution: FolderResolution | None,
    own_resolution: FolderResolution,
) -> FolderResolution:

    # If folder has its own path-derived date, always use it
    if own_resolution.source == 'path_folder':
        return own_resolution

    # If parent has a resolved date and this folder doesn't have path date,
    # this folder becomes a subfolder under the parent
    if parent_resolution and parent_resolution.resolved_date:
        if own_resolution.source != 'path_folder':
            return FolderResolution(
                bucket=None,
                resolved_date=parent_resolution.resolved_date,
                source='inherited',
                inherited_from=parent_resolution.folder_id,
                preserve_as_subfolder=True,
            )

    return own_resolution
```

**Important edge case:** If a folder path contains multiple date hierarchies (e.g., `2023/2023_10/20231015/2024/2024_09/somefolder/`), the deeper path-derived date takes precedence. This is handled naturally because folders are processed depth-first and path dates always win.

---

## Target Path Construction

### Standard Date Folders

For folders with a resolved date:

```python
def build_target_folder(
    resolved_date: int,           # YYYYMMDD
    source_folder_name: str,      # e.g., "20231015-sunset" or "peter"
    is_subfolder: bool,           # Is this under an inherited parent?
    subfolder_relative_path: str, # Relative path from inherited parent
) -> str:
    year = resolved_date // 10000
    month = (resolved_date // 100) % 100
    day = resolved_date % 100

    # Extract annotation from folder name (strip any date prefix)
    annotation = extract_annotation(source_folder_name, resolved_date)

    # Build target folder name
    if annotation:
        folder_name = f"{resolved_date}-{annotation}"
    else:
        folder_name = str(resolved_date)

    base_path = f"{year}/{year}_{month:02d}/{folder_name}"

    if is_subfolder and subfolder_relative_path:
        return f"{base_path}/{subfolder_relative_path}"

    return base_path
```

### Annotation Extraction

Strip date-like prefixes from folder names to avoid duplication:

```python
MAX_ANNOTATION_LENGTH = 10

def extract_annotation(folder_name: str, resolved_date: int) -> str | None:
    """
    Extract non-date portion of folder name for use as annotation.
    Truncates to MAX_ANNOTATION_LENGTH characters.

    Examples:
        "20231015-sunset" with date 20231015 → "sunset"
        "2023_10_15_sunset" with date 20231015 → "sunset"
        "sunset" with date 20231015 → "sunset"
        "20231015" with date 20231015 → None (no annotation needed)
        "peters-birthday-party" with date 20231015 → "peters-" (truncated)
    """
    # Pattern: optional date prefix (various formats) + optional separator + rest
    # Strip the date if it matches resolved_date
    annotation = ...  # extraction logic

    if annotation and len(annotation) > MAX_ANNOTATION_LENGTH:
        annotation = annotation[:MAX_ANNOTATION_LENGTH]

    return annotation
```

**Date patterns to strip:**
- `YYYYMMDD` (e.g., `20231015`)
- `YYYY_MM_DD` (e.g., `2023_10_15`)
- `YYYY-MM-DD` (e.g., `2023-10-15`)

Only strip if the date matches the resolved date (don't strip unrelated numbers).

**Truncation:** Annotations longer than 10 characters are truncated. This keeps target folder names manageable while preserving enough context to identify the original folder.

### Bucket Folders

For unresolved folders:

```python
def build_bucket_path(bucket: str, source_folder: str) -> str:
    """
    Preserve full original path under bucket.

    Example:
        bucket="_mixed_dates", source="2021/2021_09/peter"
        → "_mixed_dates/2021/2021_09/peter"
    """
    return f"{bucket}/{source_folder}"
```

---

## Duplicate Handling

### Detection

Within a target folder, if multiple files would have the same filename:

```python
def check_duplicate(
    target_folder: str,
    filename: str,
    existing_targets: set[str],
) -> tuple[str, bool]:
    """
    Returns (final_filename, is_potential_duplicate)
    """
    target_path = f"{target_folder}/{filename}"

    if target_path not in existing_targets:
        return filename, False

    # Generate unique filename with source folder hash
    source_hash = short_hash(source_folder, length=6)
    name, ext = split_extension(filename)
    new_filename = f"pot_dupe_{source_hash}_{name}{ext}"

    return new_filename, True
```

### Hash Function

Use a short, deterministic hash of the source folder path:

```python
def short_hash(path: str, length: int = 6) -> str:
    """Generate short hash for duplicate disambiguation."""
    import hashlib
    full_hash = hashlib.sha256(path.encode()).hexdigest()
    return full_hash[:length]
```

Example: `photo.jpg` from `/some/path/folder/` becomes `pot_dupe_a1b2c3_photo.jpg`

---

## Sidecar Detection

Flag files that appear to be sidecars:

```python
def detect_sidecar(file: FileRecord, folder_files: list[FileRecord]) -> bool:
    """
    A file is a sidecar if another file in the same folder has:
    - Same base name (filename without extension)
    - Different extension
    - Is an image file
    """
    sidecar_extensions = {'xmp', 'json', 'xml', 'thm', 'aae'}

    if file.extension not in sidecar_extensions:
        return False

    for other in folder_files:
        if other.id == file.id:
            continue
        if other.filename_base == file.filename_base:
            if other.extension in IMAGE_EXTENSIONS:
                return True

    return False
```

This is informational only — sidecars are not treated differently in planning.

---

## Configuration

### Configurable Thresholds

```python
@dataclass
class PlannerConfig:
    # Minimum percentage of images that must have dates
    # Below this, folder goes to _mixed_dates
    min_coverage_threshold: float = 0.30  # 30%

    # Minimum percentage agreement on prevalent date
    # Above this, use the prevalent date
    min_prevalence_threshold: float = 0.80  # 80%

    # Maximum date spread in calendar months
    # At or above this, folder goes to _mixed_dates
    max_date_span_months: int = 3
```

### CLI Options

```bash
# Run planner with defaults
uv run photosort plan

# Adjust thresholds
uv run photosort plan --min-coverage 0.25 --min-prevalence 0.75 --max-span 4

# Show plan statistics
uv run photosort plan --stats
```

---

## Database Schema

### Table: `folder_plan`

```sql
CREATE TABLE IF NOT EXISTS folder_plan (
    id INTEGER PRIMARY KEY,
    scan_session_id INTEGER NOT NULL,
    source_folder TEXT NOT NULL,

    -- Resolution result
    resolved_date INTEGER,                    -- YYYYMMDD, NULL if bucketed
    resolved_date_source TEXT,                -- 'path_folder', 'metadata_unanimous',
                                              -- 'metadata_prevalent', 'inherited',
                                              -- 'low_coverage', 'wide_spread', 'no_consensus', 'no_images'
    target_folder TEXT NOT NULL,
    bucket TEXT,                              -- NULL, 'mixed_dates', 'non_media'

    -- File counts
    total_file_count INTEGER NOT NULL,
    image_file_count INTEGER NOT NULL,
    images_with_date_count INTEGER NOT NULL,

    -- Coverage metrics
    date_coverage_pct REAL,                   -- images_with_date / image_file_count (NULL if no images)

    -- Date distribution (for images with dates)
    prevalent_date INTEGER,
    prevalent_date_count INTEGER,
    prevalent_date_pct REAL,                  -- prevalent_count / images_with_date (NULL if none)
    unique_date_count INTEGER,
    min_date INTEGER,
    max_date INTEGER,
    date_span_months INTEGER,

    -- Inheritance
    inherited_from_folder_id INTEGER REFERENCES folder_plan(id),
    is_subfolder BOOLEAN DEFAULT FALSE,       -- TRUE if inheriting and preserved as subfolder

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
```

### Table: `file_plan`

```sql
CREATE TABLE IF NOT EXISTS file_plan (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,
    folder_plan_id INTEGER NOT NULL REFERENCES folder_plan(id) ON DELETE CASCADE,

    -- Source (denormalized for easy querying)
    source_path TEXT NOT NULL,
    source_filename TEXT NOT NULL,

    -- File's own resolved date (before folder analysis)
    file_resolved_date INTEGER,               -- YYYYMMDD
    file_resolved_date_source TEXT,           -- 'path_folder', 'path_filename', 'exif', 'fs_modified'

    -- Target
    target_path TEXT NOT NULL,                -- Full path including filename
    target_filename TEXT NOT NULL,            -- May differ from source if pot_dupe_

    -- Flags
    is_potential_duplicate BOOLEAN DEFAULT FALSE,
    duplicate_source_hash TEXT,               -- Short hash used in filename, NULL if not duplicate
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
```

---

## Processing Order

### Algorithm

1. **Load all files** with their resolved dates (path → exif → fs_modified)
2. **Group by folder** (`directory_path`)
3. **Sort folders by depth** (shallowest first, for inheritance)
4. **For each folder:**
   - Check for path-derived date
   - If none, perform statistical analysis
   - Apply resolution rules
   - Check for inheritance from parent
   - Generate target path
5. **For each file in folder:**
   - Determine target filename (check for duplicates)
   - Detect if sidecar
   - Insert into `file_plan`

### Inheritance Resolution

Process folders in depth order (root → leaves) so parent resolutions are available:

```python
def process_folders_in_order(folders: list[str]) -> None:
    # Sort by depth (number of path separators)
    sorted_folders = sorted(folders, key=lambda f: f.count('/'))

    resolved: dict[str, FolderResolution] = {}

    for folder in sorted_folders:
        parent = get_parent_folder(folder)
        parent_resolution = resolved.get(parent)

        own_resolution = analyze_and_resolve(folder)
        final_resolution = resolve_with_inheritance(folder, parent_resolution, own_resolution)

        resolved[folder] = final_resolution
```

---

## Progress Reporting

```
Starting planning (session: 1)
Configuration: coverage=30%, prevalence=80%, max_span=3 months

Analyzing folders...
[100/2853] Analyzing folders... (45.2 folders/sec)
[200/2853] Analyzing folders... (44.8 folders/sec)
...

Planning files...
[10000/476564] Planning files... (1,234.5 files/sec)
...

Planning complete:
  Folders processed: 2,853
    - Dated (path):        1,200 (42.1%)
    - Dated (metadata):      450 (15.8%)
    - Dated (inherited):     800 (28.0%)
    - Mixed dates bucket:    350 (12.3%)
    - Non-media bucket:       53 (1.9%)

  Files planned: 476,564
    - Potential duplicates:  234 (0.05%)
    - Sidecars detected:   95,000 (19.9%)
```

---

## Analysis Queries

After planning, useful queries for understanding results:

**Folder resolution breakdown:**
```sql
SELECT
    resolved_date_source,
    bucket,
    COUNT(*) as folder_count,
    SUM(total_file_count) as total_files
FROM folder_plan
GROUP BY resolved_date_source, bucket
ORDER BY folder_count DESC;
```

**Folders that hit thresholds:**
```sql
-- Low coverage folders
SELECT source_folder, image_file_count, images_with_date_count, date_coverage_pct
FROM folder_plan
WHERE resolved_date_source = 'low_coverage'
ORDER BY image_file_count DESC
LIMIT 20;

-- Wide spread folders
SELECT source_folder, min_date, max_date, date_span_months, unique_date_count
FROM folder_plan
WHERE resolved_date_source = 'wide_spread'
ORDER BY date_span_months DESC
LIMIT 20;
```

**Threshold sensitivity analysis:**
```sql
-- What if we lowered prevalence threshold to 70%?
SELECT COUNT(*) as would_resolve
FROM folder_plan
WHERE bucket = 'mixed_dates'
  AND prevalent_date_pct >= 0.70
  AND prevalent_date_pct < 0.80;
```

**Potential duplicates:**
```sql
SELECT
    fp.source_path,
    fp.target_path,
    fp.target_filename,
    fp.duplicate_source_hash
FROM file_plan fp
WHERE fp.is_potential_duplicate = TRUE
ORDER BY fp.target_path;
```

**Sidecar distribution:**
```sql
SELECT
    f.extension,
    COUNT(*) as sidecar_count
FROM file_plan fp
JOIN files f ON fp.file_id = f.id
WHERE fp.is_sidecar = TRUE
GROUP BY f.extension
ORDER BY sidecar_count DESC;
```

**Target folder distribution:**
```sql
SELECT
    target_folder,
    COUNT(*) as file_count
FROM file_plan
GROUP BY target_folder
ORDER BY file_count DESC
LIMIT 50;
```

---

## Edge Cases

### Nested Date Hierarchies

Path: `2023/2023_10/20231015/2024/2024_09/somefolder/`

- `2023/2023_10/20231015/` resolves to `20231015` (path date)
- `2024/2024_09/somefolder/` resolves to its own date from `2024/2024_09/` structure
- Files in `somefolder` go to `2024/2024_09/<resolved>/`, not under `20231015`

This is handled correctly because path dates always take precedence over inheritance.

### Empty Folders

Folders with no files are not present in the `files` table, so they won't appear in the plan. If preservation of empty folders is needed, that's a SyncEngine concern.

### Root-Level Files

Files directly in the scan root (no subdirectory) are treated as their own "folder" with `directory_path = ""` or `.`.

---

## Future Work (Out of Scope)

The following are deferred to SyncEngine or other components:

1. **Actual file operations** — copy/move files to target
2. **Conflict resolution with existing target files** — target may not be empty
3. **Metadata export to target** — storing provenance in target location
4. **Hash-based true duplicate detection** — comparing file contents
5. **Dry-run execution** — preview what would be copied

---

## CLI Interface

```bash
# Run planner (always clears and rebuilds plan)
uv run photosort plan

# With custom thresholds
uv run photosort plan --min-coverage 0.25 --min-prevalence 0.75 --max-span 4

# Show statistics only (no planning)
uv run photosort plan --stats
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--min-coverage` | `0.30` | Minimum % of images with dates |
| `--min-prevalence` | `0.80` | Minimum % agreement for prevalent date |
| `--max-span` | `3` | Maximum date spread in calendar months |
| `--stats` | `False` | Show plan statistics and exit |

### Re-planning Behavior

The planner **always clears existing plan data** before running. This ensures consistency — if thresholds or code change, you won't have a mixture of results from different planner versions.

```python
def _clear_existing_plan(self, scan_session_id: int) -> None:
    """Clear any existing plan for this session."""
    self.db.conn.execute(
        "DELETE FROM folder_plan WHERE scan_session_id = ?",
        (scan_session_id,),
    )
    # file_plan entries cascade delete via foreign key
    self.db.conn.commit()
```
