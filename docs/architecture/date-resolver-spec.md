# DateResolver Specification

## Overview

The DateResolver is responsible for determining the "capture date" or "logical date" of files in the photo archive. This date is used to decide where files should be placed in the target directory structure.

### Design Philosophy

The DateResolver prioritizes **path-encoded dates** over metadata because:

1. **Metadata can be corrupted** — files restored from broken drives may have incorrect timestamps
2. **Intentional grouping matters** — photos taken across midnight during a night shoot should stay grouped by "shooting day," not split by technical capture time
3. **Sidecars and derivatives** — `.xmp` files, exports, and other non-image files often have creation dates unrelated to the original capture, but live alongside their source images

### Two-Pass Architecture

The DateResolver operates in two passes:

**Pass 1: Path-Based Extraction (this spec)**
- Extracts date signals from file paths using multiple strategies
- Stores all extracted signals for later analysis and conflict detection
- Fast: operates purely on path strings already in the database

**Pass 2: Metadata Extraction (future spec)**
- Runs only for files where Pass 1 yields no usable date
- Extracts EXIF data from images, metadata from videos
- Expensive: requires reading file contents
- Scope will be determined after analyzing Pass 1 results

**Sibling Inference (future spec)**
- Non-image files inherit dates from image files in the same folder
- Strictly same-level only (not subfolders)
- Runs after Pass 1 and Pass 2 to fill remaining gaps

---

## Pass 1: Path-Based Date Extraction

### Strategies

Pass 1 employs three independent strategies. Each strategy examines the file path and attempts to extract a date. All strategies run on every file, and all results are stored separately for later analysis.

#### Strategy 1: Hierarchy (`date_path_hierarchy`)

Looks for a **consecutive** `yyyy/mm/dd` folder structure at any depth in the path.

**Rules:**
- The three folders must be directly nested (no folders in between)
- There can be any folders before or after the `yyyy/mm/dd` sequence
- If multiple valid `yyyy/mm/dd` sequences exist in the path, keep the **deepest** one
- The file does not need to be directly inside the `dd` folder (subfolders are allowed)

**Examples:**

| Path | Extracted Date | Notes |
|------|----------------|-------|
| `/archive/2023/05/14/IMG_001.arw` | 20230514 | Basic case |
| `/2023/05/14/subfolder/IMG_001.arw` | 20230514 | File in subfolder below date hierarchy |
| `/projects/wedding/2023/05/14/raw/IMG_001.arw` | 20230514 | Arbitrary folders before hierarchy |
| `/2023/05/14/exports/final/IMG_001.jpg` | 20230514 | Multiple subfolders below |
| `/2023/05/2023_05_14/IMG_001.arw` | NULL | `05` and `2023_05_14` are not consecutive yyyy/mm/dd |
| `/2023/05/IMG_001.arw` | NULL | Missing day level |
| `/backup/2023/05/14/archive/2024/01/15/IMG_001.arw` | 20240115 | Two hierarchies, keep deepest |
| `/2023/13/01/IMG_001.arw` | NULL | Invalid month (13) |
| `/2023/05/32/IMG_001.arw` | NULL | Invalid day (32) |

**Folder validation:**
- Year folder: exactly 4 digits, 1900-2099
- Month folder: exactly 2 digits, 01-12
- Day folder: exactly 2 digits, 01-31

#### Strategy 2: Folder Date (`date_path_folder`)

Looks for any **single folder** in the path that encodes a complete date (year, month, day).

**Rules:**
- Every folder in the path is checked independently
- Date can appear anywhere in the folder name (prefix, suffix, middle)
- Separators between date components can be `-`, `_`, or nothing
- If multiple folders contain valid dates, keep the **deepest** one
- Store the matched folder name for debugging

**Pattern description:**
A folder matches if it contains a substring matching:
- 4-digit year (1900-2099)
- followed by optional separator (`-` or `_`)
- followed by 2-digit month (01-12)
- followed by optional separator (`-` or `_`)
- followed by 2-digit day (01-31)

The date pattern must be bounded by start/end of string or a separator (`-` or `_`), to avoid matching arbitrary 8-digit sequences.

**Examples:**

| Path | Extracted Date | Matched Folder | Notes |
|------|----------------|----------------|-------|
| `/archive/20230514/IMG_001.arw` | 20230514 | `20230514` | Compact format |
| `/archive/2023_05_14/IMG_001.arw` | 20230514 | `2023_05_14` | Underscore separated |
| `/archive/2023-05-14/IMG_001.arw` | 20230514 | `2023-05-14` | Dash separated |
| `/archive/20230514-sunset/IMG_001.arw` | 20230514 | `20230514-sunset` | Date prefix with suffix |
| `/archive/2023_05_14_a7iv/IMG_001.arw` | 20230514 | `2023_05_14_a7iv` | Date prefix with camera model |
| `/archive/sunset-20230514/IMG_001.arw` | 20230514 | `sunset-20230514` | Date suffix with prefix |
| `/archive/project_2023_05_14_final/IMG_001.arw` | 20230514 | `project_2023_05_14_final` | Date in middle |
| `/archive/2023-05-14-beach-day/IMG_001.arw` | 20230514 | `2023-05-14-beach-day` | Mixed separators in suffix |
| `/2023/05/14/20230514/IMG_001.arw` | 20230514 | `20230514` | Both strategies match, folder is deepest |
| `/20230514/20230515/IMG_001.arw` | 20230515 | `20230515` | Two date folders, keep deepest |
| `/archive/photos/IMG_001.arw` | NULL | NULL | No date folder |
| `/archive/12345678/IMG_001.arw` | NULL | NULL | 8 digits but not valid date |
| `/archive/2023051/IMG_001.arw` | NULL | NULL | Only 7 digits |
| `/archive/v20230514/IMG_001.arw` | NULL | NULL | Not bounded by separator |

#### Strategy 3: Filename Date (`date_path_filename`)

Looks for a date pattern within the filename itself.

**Rules:**
- Same pattern matching as Strategy 2 (Folder Date)
- If multiple date patterns exist in the filename, keep the **leftmost** one
- Store the filename for debugging
- Case insensitive for non-date parts of the pattern

**Examples:**

| Filename | Extracted Date | Notes |
|----------|----------------|-------|
| `IMG_20230514_143052.jpg` | 20230514 | Common phone naming |
| `20230514_IMG_001.arw` | 20230514 | Date prefix |
| `photo_2023-05-14.jpg` | 20230514 | Dash separated |
| `2023_05_14_sunset.jpg` | 20230514 | Underscore separated |
| `DSC_001.arw` | NULL | No date in filename |
| `IMG_20230514_20230515_merged.jpg` | 20230514 | Two dates, keep leftmost |
| `export_20230514.jpg` | 20230514 | Date suffix |
| `20230514.jpg` | 20230514 | Filename is just date |
| `P20230514.jpg` | NULL | Not bounded by separator |

---

## Database Schema Changes

Add the following columns to the `files` table:

```sql
-- Path-based date extraction (Pass 1)
date_path_hierarchy INTEGER,           -- YYYYMMDD from yyyy/mm/dd folder structure
date_path_hierarchy_source TEXT,       -- The matched path segment, e.g., "2023/05/14"

date_path_folder INTEGER,              -- YYYYMMDD from single folder encoding full date
date_path_folder_source TEXT,          -- The matched folder name, e.g., "20230514-sunset"

date_path_filename INTEGER,            -- YYYYMMDD from filename pattern
date_path_filename_source TEXT,        -- The filename, e.g., "IMG_20230514_143052.jpg"

-- Populated after analysis
date_path_resolved INTEGER,            -- Final resolved date from path strategies
date_path_resolved_source TEXT,        -- Which strategy was used: "hierarchy", "folder", "filename"

-- Metadata extraction (Pass 2, future)
date_exif_original_unix REAL,          -- Already exists in schema
date_exif_original INTEGER,            -- Already exists in schema
-- ... other EXIF fields already exist

-- Final resolution (after all passes)
date_resolved INTEGER,                 -- Ultimate resolved date for this file
date_resolved_source TEXT,             -- Source: "path_hierarchy", "path_folder", "path_filename", "exif", "sibling"
```

**Index recommendations:**

```sql
-- For finding files with strategy conflicts
CREATE INDEX idx_files_date_path_hierarchy ON files(date_path_hierarchy) WHERE date_path_hierarchy IS NOT NULL;
CREATE INDEX idx_files_date_path_folder ON files(date_path_folder) WHERE date_path_folder IS NOT NULL;
CREATE INDEX idx_files_date_path_filename ON files(date_path_filename) WHERE date_path_filename IS NOT NULL;

-- For finding files needing Pass 2
CREATE INDEX idx_files_no_path_date ON files(scan_session_id)
    WHERE date_path_hierarchy IS NULL
    AND date_path_folder IS NULL
    AND date_path_filename IS NULL;
```

---

## Implementation Notes

### Regex Patterns

For simplicity, regex patterns should validate:
- Year: `19\d{2}|20\d{2}` (1900-2099)
- Month: `0[1-9]|1[0-2]` (01-12)
- Day: `0[1-9]|[12]\d|3[01]` (01-31)

This allows technically invalid dates like February 31st at the regex level.

**Python code must validate actual date validity** after regex extraction. Use `datetime.date(year, month, day)` in a try/except to reject impossible dates. Edge cases like leap years should be handled correctly by this approach.

### Case Sensitivity

- Date patterns themselves are numeric, so case doesn't apply
- Folder and filename matching for non-date parts should be case-insensitive
- Store original case in `_source` columns for debugging

### Path Parsing

Use `pathlib.Path.parts` to split paths into components. This handles cross-platform path separators correctly.

For hierarchy detection, iterate through consecutive triples of path parts.

### Processing Order

1. Load all files from `files` table for a given `scan_session_id`
2. For each file, run all three strategies
3. Batch update results to database (for performance)
4. Generate summary statistics for analysis

---

## Analysis Queries

After Pass 1 completes, use these queries to understand the data:

**Files with no path-based date:**
```sql
SELECT COUNT(*) FROM files
WHERE date_path_hierarchy IS NULL
  AND date_path_folder IS NULL
  AND date_path_filename IS NULL;
```

**Strategy agreement:**
```sql
SELECT
    date_path_hierarchy,
    date_path_folder,
    date_path_filename,
    COUNT(*) as file_count
FROM files
WHERE date_path_hierarchy IS NOT NULL
   OR date_path_folder IS NOT NULL
   OR date_path_filename IS NOT NULL
GROUP BY date_path_hierarchy, date_path_folder, date_path_filename
ORDER BY file_count DESC;
```

**Conflicts between strategies:**
```sql
SELECT
    source_path,
    date_path_hierarchy,
    date_path_hierarchy_source,
    date_path_folder,
    date_path_folder_source,
    date_path_filename,
    date_path_filename_source
FROM files
WHERE date_path_hierarchy IS NOT NULL
  AND date_path_folder IS NOT NULL
  AND date_path_hierarchy != date_path_folder;
```

**Files by extension needing metadata extraction:**
```sql
SELECT extension, COUNT(*) as file_count
FROM files
WHERE date_path_hierarchy IS NULL
  AND date_path_folder IS NULL
  AND date_path_filename IS NULL
GROUP BY extension
ORDER BY file_count DESC;
```

---

## Future Work (Out of Scope)

The following are explicitly deferred to future specs:

1. **Pass 2: Metadata Extraction** — EXIF parsing for images, video metadata extraction
2. **Sibling Inference** — inheriting dates from image files in the same folder
3. **Conflict Resolution Rules** — deciding which strategy wins when they disagree
4. **`date_resolved` Population** — the final resolved date column
5. **Target Path Generation** — using resolved dates to determine destination paths

These will be specified after analyzing Pass 1 results from real data.
