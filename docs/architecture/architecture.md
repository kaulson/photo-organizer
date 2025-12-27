# Photo Organizer Architecture Document

## Project Overview

A Python-based tool for consolidating, classifying, deduplicating, and organizing ~6TB+ of photography and personal files scattered across multiple drives into a unified, well-structured archive.

### Primary Goal

**No file left behind.** Every file must be safely copied to the target drive. Deduplication and perfect structure are secondary optimizations—if the choice is between losing a file or keeping a duplicate, we keep the duplicate.

### Secondary Goals

- Classify files by type (camera photo, phone photo, screenshot, video, document, etc.)
- Enrich metadata from multiple sources (EXIF, path patterns, filesystem)
- Deduplicate byte-identical files efficiently
- Build a clean `yyyy/mm/dd` structure for photography files
- Preserve meaningful structure for non-photography files
- Support iterative exploration and rule refinement

---

## Core Principles

1. **Immutability of scan data**: Scanning produces immutable records. Planning reads this data but never mutates it.

2. **Separation of phases**: Scan → Classify → Plan → Execute are distinct operations that can be run independently.

3. **Resumability**: All long-running operations can be interrupted and resumed without data loss.

4. **Implicit canonical behavior**: The first drive's structure becomes canonical not through explicit configuration, but simply because it's transferred first. Subsequent drives merge into what already exists on target.

5. **Buckets for uncertainty**: Files that can't be automatically classified or placed go into named buckets for later manual review, rather than being lost or causing failures.

6. **Verification over trust**: All copies are verified. We don't assume the filesystem got it right.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLI Interface                          │
│  scan | status | classify | plan | preview | execute | query    │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Core Components                          │
│  ┌───────────┐ ┌────────────┐ ┌──────────────┐ ┌─────────────┐ │
│  │  Scanner  │ │ Classifier │ │ DateResolver │ │Deduplicator │ │
│  └───────────┘ └────────────┘ └──────────────┘ └─────────────┘ │
│  ┌───────────┐ ┌────────────┐ ┌──────────────┐                 │
│  │  Planner  │ │  Executor  │ │   Verifier   │                 │
│  └───────────┘ └────────────┘ └──────────────┘                 │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      SQLite Database                            │
│  files | scan_sessions | plans | execution_log | duplicates     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### Scanner

**Responsibility**: Walk filesystem, extract metadata, populate database.

**Behavior**:
- Recursively walks source path
- For each file: extracts size, filesystem dates, computes quick hash if needed
- Extracts EXIF/metadata using exiftool
- Writes to database in batches (every 100-1000 files) for resumability
- Skips files already in database (by path + mtime) for incremental scanning
- Records scan session with start time, source path, completion status

**Resumability**: If interrupted, the next run detects incomplete session and offers to resume from last committed batch.

**External dependency**: `exiftool` (Perl) via `pyexiftool` wrapper.

---

### Classifier

**Responsibility**: Determine file type category for each scanned file.

**Categories**:

| Category | Description | Structure Treatment |
|----------|-------------|---------------------|
| `camera_raw` | RAW files (CR2, NEF, ARW, DNG, ORF, RAF, etc.) | Photography: yyyy/mm/dd |
| `camera_jpeg` | JPEGs from cameras (non-phone) | Photography: yyyy/mm/dd |
| `camera_video` | Video from cameras/drones | Photography: yyyy/mm/dd |
| `phone_photo` | Photos from mobile phones | Phone: yyyy/mm/dd (separate root) |
| `phone_video` | Videos from mobile phones | Phone: yyyy/mm/dd (separate root) |
| `screenshot` | Screen captures | Non-photography: path-preserving |
| `edited` | Photoshop files, other edits (.psd, .psb, .tif from editors) | Photography: yyyy/mm/dd (by original/creation date) |
| `sidecar` | XMP, THM, and other metadata files | Travels with parent file |
| `document` | PDFs, Office files, text files | Non-photography: path-preserving |
| `other` | Everything else | Non-photography: path-preserving |

**Classification Logic** (applied in order):

1. **By extension**: RAW formats → `camera_raw`, `.psd/.psb` → `edited`, `.xmp/.thm` → `sidecar`, document extensions → `document`

2. **By filename pattern**: Screenshot patterns (e.g., `Screenshot_*`, `Screen Shot *`, platform-specific patterns) → `screenshot`

3. **By EXIF make/model** (for images/video):
   - Phone makes (Apple, Samsung, Google, OnePlus, Xiaomi, Huawei, etc.) → `phone_photo` or `phone_video`
   - Known camera makes → `camera_jpeg` or `camera_video`
   - Drone makes (DJI, etc.) → `camera_video`

4. **Fallback**: If no EXIF and not identifiable by filename → `unknown_type` bucket

**Configuration**: Phone makes list is configurable to handle edge cases.

---

### DateResolver

**Responsibility**: Extract all date signals from a file and apply strategy to choose canonical date.

**Date signals extracted** (in typical priority order):

| Signal | Source | Reliability |
|--------|--------|-------------|
| `date_exif_original` | EXIF DateTimeOriginal | High - shutter click time |
| `date_exif_create` | EXIF CreateDate | High |
| `date_exif_modify` | EXIF ModifyDate | Medium - may reflect edits |
| `date_path_derived` | Parsed from path (yyyy/mm/dd patterns) | Medium - reflects past organization intent |
| `date_filesystem_modified` | File mtime | Low - often reflects copy time |
| `date_filesystem_created` | File ctime/birthtime | Low - filesystem dependent |

**Path parsing patterns**:
- `yyyy/mm/dd/` or `yyyy/mm/yyyy-mm-dd/` → extract date
- `yyyy/mm/yyyymmdd/` → extract date
- `yyyymmdd` in folder name → extract date
- Configurable regex patterns for custom structures

**Strategies**:

| Strategy | Behavior |
|----------|----------|
| `path_first` | Prefer path-derived date, fall back to EXIF, then filesystem |
| `exif_first` | Prefer EXIF original, fall back to path, then filesystem |
| `newest` | Use the most recent date from all signals |
| `oldest` | Use the oldest date from all signals |

**Rationale for `path_first` as default**: Past organizational decisions often encode intent (e.g., night sky session spanning midnight kept in one folder). EXIF is "more accurate" but path may be "more correct" for organization purposes.

**Output**: `date_chosen` field populated based on strategy. All original signals preserved for later re-evaluation.

---

### Deduplicator

**Responsibility**: Identify byte-identical files efficiently.

**Algorithm** (progressive, optimized for minimal I/O):

```
1. Group files by size
   └─ Unique sizes → mark as "no duplicate possible"
   └─ Same-size groups → proceed to step 2

2. For same-size files, compute hash of first 64KB
   └─ Unique hashes → mark as "no duplicate possible"
   └─ Same hash → proceed to step 3

3. For still-matching files, compute hash of last 64KB
   └─ Unique hashes → mark as "no duplicate possible"
   └─ Same hash → proceed to step 4

4. Compute full file hash (SHA-256)
   └─ Files with identical full hash are duplicates
```

**Performance characteristics**:
- Step 1: Metadata only, no I/O
- Step 2: Read 64KB per file in collision groups
- Step 3: Read 64KB per file in remaining collisions (seekable)
- Step 4: Full read, but only for tiny fraction of files

**Small file optimization**: Files below configurable threshold (default 1MB) skip partial hashing and go directly to full hash.

**Duplicate handling**: When duplicates are found, one is marked as "keeper" (based on path preference or first-seen), others marked as "duplicate_of" with reference to keeper's ID.

---

### Planner

**Responsibility**: Generate target paths for all files based on classification, dates, and existing target state.

**Photography files** (`camera_*`, `phone_*`, `edited`):

```
Target structure:
{target_root}/{photography_root}/{yyyy}/{mm}/{dd}/{original_leaf_folder}/

Example:
/mnt/target/Photography/2019/03/15/spring_shoot/IMG_1234.CR2
```

- `yyyy/mm/dd` from `date_chosen`
- `original_leaf_folder` is the immediate parent folder name from source (preserves shoot/session naming)
- If no date available → `no_date` bucket

**Phone files** (same structure, different root):

```
{target_root}/{phone_root}/{yyyy}/{mm}/{dd}/{original_leaf_folder}/
```

**Sidecar files**:

- Match to parent file by: same directory + same basename + known sidecar extension
- Travel with parent file to same target location
- If parent not found → `orphan_sidecar` bucket
- If multiple potential parents across drives with matching paths → create copy with each parent

**Non-photography files** (`screenshot`, `document`, `other`):

```
Target structure:
{target_root}/{other_files_root}/{preserved_path}/

Example:
Source: /mnt/drive2/backup/Documents/Work/report.pdf
Target: /mnt/target/Archive/Documents/Work/report.pdf
```

**Path suffix merging** (for non-photography):

When scanning subsequent drives, if a path shares the last 3+ directory levels with an existing path on target, merge into the existing structure:

```
Already on target: Archive/Documents/Work/Projects/
New file source:   OldBackup/stuff/Work/Projects/report.pdf
                              ^^^^^^^^^^^^^^^^^^^^
                              Shares 3 levels: Work/Projects/report.pdf

Result: Archive/Documents/Work/Projects/report.pdf
```

- Minimum shared depth: 3 levels (configurable)
- If ambiguous (multiple matches) → don't auto-merge, use full source path
- If no match → preserve source path structure under `other_files_root`

**Filename conflicts**:

When two different files would have the same target path:
- Append suffix: `filename.ext` → `filename_001.ext`, `filename_002.ext`
- Log the conflict for review

**Duplicates**:

- Keeper file: gets target path as normal
- Duplicate files: skipped (not copied), logged as "duplicate of {keeper_path}"

---

### Executor

**Responsibility**: Perform the actual file copies according to plan.

**Behavior**:
- Reads plan from database
- Creates target directories as needed
- Copies files (preserving timestamps)
- Verifies each copy
- Logs all operations
- Updates execution_log table
- Commits progress in batches for resumability

**Copy verification** (configurable modes):

| Mode | Method | Speed | Confidence |
|------|--------|-------|------------|
| `size_only` | Compare file sizes | Fastest | Catches gross failures |
| `partial` | Size + hash first/last 64KB | Fast | Very high confidence |
| `full` | Full SHA-256 comparison | Slow | Absolute certainty |

Default: `partial`

**Error handling**:
- Failed copies logged to error log
- Execution continues with other files
- Failed files can be retried

---

## Data Model

### SQLite Schema

```sql
-- Scan session tracking
CREATE TABLE scan_sessions (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status TEXT NOT NULL,  -- 'running', 'completed', 'interrupted'
    files_scanned INTEGER DEFAULT 0
);

-- Core file inventory
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    scan_session_id INTEGER REFERENCES scan_sessions(id),

    -- Source identification
    source_path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    extension TEXT,
    size INTEGER NOT NULL,

    -- Filesystem metadata
    fs_modified TIMESTAMP,
    fs_created TIMESTAMP,

    -- Hashes (computed progressively)
    hash_quick_start TEXT,      -- First 64KB
    hash_quick_end TEXT,        -- Last 64KB
    hash_full TEXT,             -- Full SHA-256

    -- Extracted metadata
    date_exif_original TIMESTAMP,
    date_exif_create TIMESTAMP,
    date_exif_modify TIMESTAMP,
    date_path_derived TIMESTAMP,

    -- Classification
    file_type TEXT,             -- camera_raw, phone_photo, etc.
    exif_make TEXT,
    exif_model TEXT,

    -- Flexible metadata storage
    metadata_json TEXT,         -- JSON blob for all other extracted data

    -- Processing state
    classified_at TIMESTAMP,

    -- Timestamps
    scanned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX idx_files_size ON files(size);
CREATE INDEX idx_files_hash_quick_start ON files(hash_quick_start);
CREATE INDEX idx_files_hash_full ON files(hash_full);
CREATE INDEX idx_files_file_type ON files(file_type);
CREATE INDEX idx_files_extension ON files(extension);

-- Duplicate tracking
CREATE TABLE duplicates (
    id INTEGER PRIMARY KEY,
    file_id INTEGER REFERENCES files(id),
    keeper_id INTEGER REFERENCES files(id),
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sidecar relationships
CREATE TABLE sidecars (
    id INTEGER PRIMARY KEY,
    sidecar_file_id INTEGER REFERENCES files(id),
    parent_file_id INTEGER REFERENCES files(id),
    match_confidence TEXT  -- 'exact', 'basename_only', etc.
);

-- Plans (separate from scan data, regenerable)
CREATE TABLE plans (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    strategy TEXT NOT NULL,     -- Strategy used to generate plan
    status TEXT NOT NULL        -- 'draft', 'approved', 'executed'
);

CREATE TABLE plan_items (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER REFERENCES plans(id),
    file_id INTEGER REFERENCES files(id),
    action TEXT NOT NULL,       -- 'copy', 'skip_duplicate', 'bucket'
    target_path TEXT,
    bucket_name TEXT,           -- If action is 'bucket'
    reasoning TEXT,             -- Human-readable explanation
    date_chosen TIMESTAMP,
    date_strategy_used TEXT
);

-- Execution log (audit trail)
CREATE TABLE execution_log (
    id INTEGER PRIMARY KEY,
    plan_item_id INTEGER REFERENCES plan_items(id),
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_path TEXT NOT NULL,
    target_path TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,       -- 'success', 'failed', 'skipped'
    verification_mode TEXT,
    verification_passed BOOLEAN,
    error_message TEXT,
    bytes_copied INTEGER
);
```

---

## CLI Interface

### Commands

```bash
# Scanning
photosort scan /mnt/source_drive
photosort scan --resume                    # Resume interrupted scan
photosort status                           # Show scan progress, file counts

# Classification (runs on scanned files)
photosort classify                         # Run classification on unclassified files
photosort classify --reclassify            # Re-run on all files

# Planning
photosort plan                             # Generate plan with default strategy
photosort plan --strategy exif_first       # Use specific date strategy
photosort preview                          # Show summary of what plan would do
photosort preview --detailed               # Show file-by-file preview

# Execution
photosort execute                          # Execute current plan
photosort execute --dry-run                # Simulate execution, log but don't copy
photosort execute --verify full            # Execute with full hash verification

# Exploration
photosort query "SELECT * FROM files WHERE file_type IS NULL LIMIT 20"
photosort buckets                          # Show bucket contents summary
photosort duplicates                       # Show duplicate groups

# Utilities
photosort init                             # Initialize database and config
photosort config                           # Show current configuration
```

### Example Workflow

```bash
# First time setup
photosort init
# Edit ~/.photosort/config.yaml to set target_root, etc.

# Scan first drive
photosort scan /mnt/drive1
photosort status
# "Scanned 2,847,293 files"

# Explore and understand the data
photosort query "SELECT file_type, COUNT(*) FROM files GROUP BY file_type"
photosort query "SELECT * FROM files WHERE date_exif_original IS NULL LIMIT 50"

# Run classification
photosort classify
photosort buckets
# "unknown_type: 12,847 files"

# Generate and preview plan
photosort plan --strategy path_first
photosort preview
# "Would copy 2,834,446 files, skip 12,847 duplicates, bucket 12,847 unknown"

# Execute
photosort execute
# Progress bar, verification, logging...

# Scan next drive (target now has data from drive1)
photosort scan /mnt/drive2
photosort classify
photosort plan
photosort preview
# Shows how drive2 files merge with existing structure
photosort execute
```

---

## Configuration

**Location**: `~/.photosort/config.yaml`

```yaml
# Database
database_path: ~/.photosort/catalog.db

# Logging
log_dir: ~/.photosort/logs/
log_level: INFO  # DEBUG, INFO, WARNING, ERROR

# Target drive
target_root: /mnt/target_drive/

# Structure roots (relative to target_root)
photography_root: Photography/
phone_root: Phone/
other_files_root: Archive/

# Classification settings
phone_makes:
  - Apple
  - Samsung
  - Google
  - OnePlus
  - Xiaomi
  - Huawei
  - Motorola
  - LG
  - Sony  # Note: Sony also makes cameras, classification uses model patterns

screenshot_patterns:
  - "Screenshot_*"
  - "Screen Shot *"
  - "Bildschirmfoto *"
  - "*_screenshot_*"

# Date resolution
default_date_strategy: path_first  # path_first, exif_first, newest, oldest

path_date_patterns:
  - "(?P<year>\\d{4})/(?P<month>\\d{2})/(?P<day>\\d{2})"
  - "(?P<year>\\d{4})/(?P<month>\\d{2})/(?P<year2>\\d{4})(?P<month2>\\d{2})(?P<day>\\d{2})"
  - "(?P<year>\\d{4})(?P<month>\\d{2})(?P<day>\\d{2})"

# Deduplication
min_size_for_partial_hash: 1048576  # 1MB
partial_hash_bytes: 65536           # 64KB

# Path merging (non-photography)
min_shared_path_depth: 3

# Verification
default_verification_mode: partial  # full, partial, size_only

# Batch sizes (for resumability)
scan_batch_size: 500
execute_batch_size: 100

# Buckets (relative to target_root)
buckets:
  no_date: _buckets/no_date/
  date_conflict: _buckets/date_conflict/
  orphan_sidecar: _buckets/orphan_sidecar/
  unknown_type: _buckets/unknown_type/
```

---

## Logging Strategy

### Log Files

| Log | Purpose | Retention |
|-----|---------|-----------|
| `scan_{timestamp}.log` | Detailed scan operations, warnings, errors | Keep all |
| `classify_{timestamp}.log` | Classification decisions and reasoning | Keep all |
| `plan_{timestamp}.log` | Plan generation details | Keep all |
| `execute_{timestamp}.log` | Copy operations, verification results | Keep all |
| `error.log` | All errors across operations (append mode) | Keep all |

### Log Levels

- **DEBUG**: Every file processed, every decision made
- **INFO**: Progress milestones, summary statistics
- **WARNING**: Unexpected but handled situations (missing metadata, parse failures)
- **ERROR**: Failures requiring attention (copy failed, verification failed)

### Audit Trail

The `execution_log` table provides a queryable audit trail:
- Every copy operation recorded with source, destination, timestamp
- Verification results recorded
- Errors captured with messages
- Can reconstruct "what happened to file X" post-facto

---

## Testing Strategy

### Synthetic Test Data

Create a script that generates a test filesystem structure with:
- Various file types (actual small images, fake RAWs by extension, sidecars)
- Different date scenarios (EXIF present/missing, path-encoded, conflicts)
- Duplicate files (byte-identical)
- Edge cases: unicode filenames, deep nesting, empty files, very long paths
- Screenshot patterns
- Phone vs camera EXIF

### Test Modes

1. **Unit tests**: Individual components (DateResolver, Classifier, path merging logic)

2. **Integration tests**: Full scan → classify → plan → execute on synthetic data

3. **Dry-run on real data**: `photosort execute --dry-run` shows what would happen without copying

4. **Small sample first**: Copy synthetic test data to a temp location, verify manually, then scale up

### Verification Tests

- **Idempotency**: Run twice with same input, second run should do nothing
- **Resumability**: Interrupt mid-scan, resume, verify no files lost or duplicated
- **Duplicate detection**: Known duplicates are correctly identified
- **Round-trip**: Source file hash matches destination file hash

---

## External Dependencies

### Required

| Dependency | Purpose | Installation |
|------------|---------|--------------|
| Python 3.11+ | Runtime | System/pyenv |
| exiftool | Metadata extraction | `apt install exiftool` / `brew install exiftool` |

### Python Packages

| Package | Purpose |
|---------|---------|
| `pyexiftool` | Python wrapper for exiftool |
| `click` | CLI framework |
| `pyyaml` | Config file parsing |
| `rich` | Terminal output, progress bars |
| `textual` | TUI interface (future) |

### Optional

| Dependency | Purpose |
|------------|---------|
| `mediainfo` | Enhanced video metadata (if exiftool insufficient) |

---

## Future Enhancements

Not in scope for initial implementation, but considered in design:

1. **TUI interface**: Using Textual for interactive exploration and decision-making

2. **Cross-drive sidecar reunification**: Find orphaned sidecars that match files on other drives

3. **Content-aware deduplication**: Perceptual hashing to find visually similar images

4. **Lightroom/Capture One integration**: Parse catalog files to understand edit history

5. **Web interface**: For reviewing buckets and making manual decisions

6. **Watch mode**: Monitor a folder for new files and auto-process

7. **Cloud backup integration**: Sync to B2/S3 after local organization

---

## Open Questions

To be resolved during implementation/exploration:

1. **Handling of files with only filesystem dates**: How aggressive to be about using unreliable dates vs bucketing?

2. **Phone make/model edge cases**: Sony makes both phones and cameras. Need model-level patterns.

3. **RAW+JPEG pairs**: Should these be linked like sidecars, or treated as independent files?

4. **Date conflict threshold**: How much disagreement between signals constitutes a "conflict" worth bucketing?

5. **Performance at scale**: May need to optimize database queries or add caching for 10M+ files.

---

## Glossary

| Term | Definition |
|------|------------|
| **Bucket** | A holding area for files that couldn't be automatically processed |
| **Keeper** | In a duplicate group, the file chosen to be copied (others are skipped) |
| **Sidecar** | A metadata file that belongs to another file (XMP, THM, etc.) |
| **Quick hash** | Partial hash of file start/end for fast duplicate detection |
| **Strategy** | A named approach for making decisions (e.g., date_strategy: path_first) |
| **Leaf folder** | The immediate parent folder of a file |
