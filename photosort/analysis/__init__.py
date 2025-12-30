"""Data analysis module for investigating date resolution patterns.

This module is intentionally isolated from other photosort components.
It only depends on the database connection to minimize coupling.
"""

# pylint: disable=inconsistent-quotes

import builtins
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class AnalysisConfig:
    """Configuration for analysis output."""

    sample_limit: int = 10
    top_n: int = 20


# Make print flush immediately for real-time output
_original_print = builtins.print


def _flush_print(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Print with immediate flush."""
    kwargs.setdefault("flush", True)
    _original_print(*args, **kwargs)


# Override print in this module
print = _flush_print  # noqa: A001  # pylint: disable=redefined-builtin


def run_full_analysis(db_path: Path, config: AnalysisConfig | None = None) -> None:
    """Run all analysis sections and print results."""
    if config is None:
        config = AnalysisConfig()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print("=" * 80)
    print("DATE RESOLUTION ANALYSIS REPORT")
    print("=" * 80)

    _analyze_coverage(conn, config)
    _analyze_strategy_agreement(conn, config)
    _analyze_conflicts(conn, config)
    _analyze_dateless_files(conn, config)
    _analyze_date_sanity(conn, config)
    _analyze_folders(conn, config)
    _analyze_source_columns(conn, config)

    conn.close()


def _print_section(title: str) -> None:
    """Print a section header."""
    print()
    print("-" * 80)
    print(f"## {title}")
    print("-" * 80)


def _print_subsection(title: str) -> None:
    """Print a subsection header."""
    print()
    print(f"### {title}")


def _pct(num: int, total: int) -> str:
    """Format a percentage string."""
    if total == 0:
        return "N/A"
    return f"{num:,} ({num / total * 100:.1f}%)"


def _analyze_coverage(conn: sqlite3.Connection, config: AnalysisConfig) -> None:
    """Analyze coverage and strategy effectiveness."""
    _print_section("1. COVERAGE & STRATEGY EFFECTIVENESS")

    # Total files
    total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    print(f"\nTotal files scanned: {total:,}")

    # Files with at least one path date
    with_any_date = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE date_path_hierarchy IS NOT NULL
           OR date_path_folder IS NOT NULL
           OR date_path_filename IS NOT NULL
    """
    ).fetchone()[0]

    without_date = total - with_any_date
    print(f"Files with at least one path date: {_pct(with_any_date, total)}")
    print(f"Files with no path date at all: {_pct(without_date, total)}")

    _print_subsection("Individual Strategy Coverage")

    hierarchy_count = conn.execute(
        "SELECT COUNT(*) FROM files WHERE date_path_hierarchy IS NOT NULL"
    ).fetchone()[0]
    folder_count = conn.execute(
        "SELECT COUNT(*) FROM files WHERE date_path_folder IS NOT NULL"
    ).fetchone()[0]
    filename_count = conn.execute(
        "SELECT COUNT(*) FROM files WHERE date_path_filename IS NOT NULL"
    ).fetchone()[0]

    print(f"  Hierarchy (yyyy/mm/dd): {_pct(hierarchy_count, total)}")
    print(f"  Folder date:           {_pct(folder_count, total)}")
    print(f"  Filename date:         {_pct(filename_count, total)}")

    _print_subsection("Strategy Combinations")

    combos = conn.execute(
        """
        SELECT
            CASE WHEN date_path_hierarchy IS NOT NULL THEN 'H' ELSE '-' END ||
            CASE WHEN date_path_folder IS NOT NULL THEN 'F' ELSE '-' END ||
            CASE WHEN date_path_filename IS NOT NULL THEN 'N' ELSE '-' END AS combo,
            COUNT(*) as cnt
        FROM files
        GROUP BY combo
        ORDER BY cnt DESC
    """
    ).fetchall()

    print("  Combo (H=hierarchy, F=folder, N=filename):")
    for row in combos:
        print(f"    {row['combo']}: {row['cnt']:,} ({row['cnt'] / total * 100:.1f}%)")

    _print_subsection("Coverage by Extension")

    ext_coverage = conn.execute(
        """
        SELECT
            LOWER(COALESCE(extension, '(none)')) as ext,
            COUNT(*) as total,
            SUM(CASE WHEN date_path_resolved IS NOT NULL THEN 1 ELSE 0 END) as with_date
        FROM files
        GROUP BY LOWER(COALESCE(extension, '(none)'))
        ORDER BY total DESC
        LIMIT ?
    """,
        (config.top_n,),
    ).fetchall()

    print(f"  Top {config.top_n} extensions by file count:")
    print(f"  {'Extension':<12} {'Total':>10} {'With Date':>12} {'Coverage':>10}")
    for row in ext_coverage:
        cov = row["with_date"] / row["total"] * 100 if row["total"] > 0 else 0
        print(f"  {row['ext']:<12} {row['total']:>10,} {row['with_date']:>12,} {cov:>9.1f}%")


def _analyze_strategy_agreement(
    conn: sqlite3.Connection, config: AnalysisConfig  # pylint: disable=unused-argument
) -> None:
    """Analyze agreement between strategies."""
    _print_section("2. STRATEGY AGREEMENT & CONFLICTS")

    # Files with multiple strategies
    multi_strategy = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE (CASE WHEN date_path_hierarchy IS NOT NULL THEN 1 ELSE 0 END +
               CASE WHEN date_path_folder IS NOT NULL THEN 1 ELSE 0 END +
               CASE WHEN date_path_filename IS NOT NULL THEN 1 ELSE 0 END) >= 2
    """
    ).fetchone()[0]

    print(f"\nFiles with 2+ strategies: {multi_strategy:,}")

    if multi_strategy == 0:
        print("No multi-strategy files to analyze for conflicts.")
        return

    # All agree
    all_agree = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE (CASE WHEN date_path_hierarchy IS NOT NULL THEN 1 ELSE 0 END +
               CASE WHEN date_path_folder IS NOT NULL THEN 1 ELSE 0 END +
               CASE WHEN date_path_filename IS NOT NULL THEN 1 ELSE 0 END) >= 2
        AND (date_path_hierarchy IS NULL OR date_path_folder IS NULL
             OR date_path_hierarchy = date_path_folder)
        AND (date_path_hierarchy IS NULL OR date_path_filename IS NULL
             OR date_path_hierarchy = date_path_filename)
        AND (date_path_folder IS NULL OR date_path_filename IS NULL
             OR date_path_folder = date_path_filename)
    """
    ).fetchone()[0]

    conflicts = multi_strategy - all_agree
    print(f"Files where all strategies agree: {_pct(all_agree, multi_strategy)}")
    print(f"Files with conflicts: {_pct(conflicts, multi_strategy)}")

    _print_subsection("Conflict Breakdown")

    # Hierarchy vs Folder
    hf_conflict = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE date_path_hierarchy IS NOT NULL
          AND date_path_folder IS NOT NULL
          AND date_path_hierarchy != date_path_folder
    """
    ).fetchone()[0]

    # Hierarchy vs Filename
    hn_conflict = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE date_path_hierarchy IS NOT NULL
          AND date_path_filename IS NOT NULL
          AND date_path_hierarchy != date_path_filename
    """
    ).fetchone()[0]

    # Folder vs Filename
    fn_conflict = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE date_path_folder IS NOT NULL
          AND date_path_filename IS NOT NULL
          AND date_path_folder != date_path_filename
    """
    ).fetchone()[0]

    # All three disagree
    all_disagree = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE date_path_hierarchy IS NOT NULL
          AND date_path_folder IS NOT NULL
          AND date_path_filename IS NOT NULL
          AND date_path_hierarchy != date_path_folder
          AND date_path_hierarchy != date_path_filename
          AND date_path_folder != date_path_filename
    """
    ).fetchone()[0]

    print(f"  Hierarchy ≠ Folder:   {hf_conflict:,}")
    print(f"  Hierarchy ≠ Filename: {hn_conflict:,}")
    print(f"  Folder ≠ Filename:    {fn_conflict:,}")
    print(f"  All three disagree:   {all_disagree:,}")


def _analyze_conflicts(conn: sqlite3.Connection, config: AnalysisConfig) -> None:
    """Deep dive into conflict examples."""
    _print_section("3. CONFLICT DEEP DIVES")

    _print_subsection("Sample: Hierarchy ≠ Folder")

    samples = conn.execute(
        """
        SELECT source_path, date_path_hierarchy, date_path_folder,
               date_path_hierarchy_source, date_path_folder_source
        FROM files
        WHERE date_path_hierarchy IS NOT NULL
          AND date_path_folder IS NOT NULL
          AND date_path_hierarchy != date_path_folder
        LIMIT ?
    """,
        (config.sample_limit,),
    ).fetchall()

    if samples:
        for row in samples:
            print(f"  Path: {row['source_path']}")
            h_date = row["date_path_hierarchy"]
            h_src = row["date_path_hierarchy_source"]
            print(f"    Hierarchy: {h_date} (from {h_src})")
            f_date = row["date_path_folder"]
            f_src = row["date_path_folder_source"]
            print(f"    Folder:    {f_date} (from {f_src})")
            print()
    else:
        print("  No conflicts found.")

    _print_subsection("Sample: Folder ≠ Filename")

    samples = conn.execute(
        """
        SELECT source_path, filename_full, date_path_folder, date_path_filename,
               date_path_folder_source, date_path_filename_source
        FROM files
        WHERE date_path_folder IS NOT NULL
          AND date_path_filename IS NOT NULL
          AND date_path_folder != date_path_filename
        LIMIT ?
    """,
        (config.sample_limit,),
    ).fetchall()

    if samples:
        for row in samples:
            print(f"  Path: {row['source_path']}")
            fld_date = row["date_path_folder"]
            fld_src = row["date_path_folder_source"]
            print(f"    Folder:   {fld_date} (from {fld_src})")
            fn_date = row["date_path_filename"]
            fn_src = row["date_path_filename_source"]
            print(f"    Filename: {fn_date} (from {fn_src})")
            print()
    else:
        print("  No conflicts found.")

    _print_subsection("Conflict Concentration by Directory")

    conflict_dirs = conn.execute(
        """
        SELECT directory_path, COUNT(*) as conflict_count
        FROM files
        WHERE (date_path_hierarchy IS NOT NULL AND date_path_folder IS NOT NULL
               AND date_path_hierarchy != date_path_folder)
           OR (date_path_hierarchy IS NOT NULL AND date_path_filename IS NOT NULL
               AND date_path_hierarchy != date_path_filename)
           OR (date_path_folder IS NOT NULL AND date_path_filename IS NOT NULL
               AND date_path_folder != date_path_filename)
        GROUP BY directory_path
        ORDER BY conflict_count DESC
        LIMIT ?
    """,
        (config.top_n,),
    ).fetchall()

    if conflict_dirs:
        print(f"  Top {config.top_n} directories with conflicts:")
        for row in conflict_dirs:
            cnt = row["conflict_count"]
            path = row["directory_path"]
            print(f"    {cnt:>5} conflicts: {path}")
    else:
        print("  No conflict directories found.")


def _analyze_dateless_files(conn: sqlite3.Connection, config: AnalysisConfig) -> None:
    """Analyze files without any path date."""
    _print_section("4. FILES WITH NO PATH DATE")

    dateless_count = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE date_path_resolved IS NULL
    """
    ).fetchone()[0]

    print(f"\nTotal dateless files: {dateless_count:,}")

    if dateless_count == 0:
        print("All files have path dates!")
        return

    _print_subsection("Top Extensions Without Path Date")

    ext_dateless = conn.execute(
        """
        SELECT LOWER(COALESCE(extension, '(none)')) as ext, COUNT(*) as cnt
        FROM files
        WHERE date_path_resolved IS NULL
        GROUP BY LOWER(COALESCE(extension, '(none)'))
        ORDER BY cnt DESC
        LIMIT ?
    """,
        (config.top_n,),
    ).fetchall()

    for row in ext_dateless:
        print(f"  {row['ext']:<12} {row['cnt']:>10,}")

    _print_subsection("Sample Directories with Dateless Image Files")

    # Common image extensions (without dots, as stored in DB)
    image_exts = (
        "('jpg', 'jpeg', 'png', 'arw', 'nef', 'cr2', " "'dng', 'heic', 'tiff', 'tif', 'raw', 'raf')"
    )

    dateless_dirs = conn.execute(
        f"""
        SELECT directory_path, COUNT(*) as cnt
        FROM files
        WHERE date_path_resolved IS NULL
          AND LOWER(extension) IN {image_exts}
        GROUP BY directory_path
        ORDER BY cnt DESC
        LIMIT ?
    """,
        (config.top_n,),
    ).fetchall()

    if dateless_dirs:
        for row in dateless_dirs:
            cnt = row["cnt"]
            path = row["directory_path"]
            print(f"  {cnt:>5} files: {path}")
    else:
        print("  No dateless image directories found.")

    _print_subsection("Sibling Inference Candidates")

    # Dateless images in folders that have dated images
    # Using a JOIN approach which is often faster than EXISTS for SQLite
    print("  Querying sibling candidates...", end=" ")
    sibling_candidates = conn.execute(
        f"""
        SELECT COUNT(*) as candidate_count
        FROM (
            SELECT DISTINCT f1.id
            FROM files f1
            INNER JOIN files f2 ON f2.directory_path = f1.directory_path
            WHERE f1.date_path_resolved IS NULL
              AND LOWER(f1.extension) IN {image_exts}
              AND f2.date_path_resolved IS NOT NULL
              AND LOWER(f2.extension) IN {image_exts}
              AND f1.id != f2.id
        )
    """
    ).fetchone()[0]
    print("done.")

    print(f"  Dateless image files with dated siblings: {sibling_candidates:,}")


def _analyze_date_sanity(conn: sqlite3.Connection, config: AnalysisConfig) -> None:
    """Check extracted dates for sanity."""
    _print_section("5. DATE SANITY CHECKS")

    today_int = int(date.today().strftime("%Y%m%d"))

    _print_subsection("Date Range")

    date_range = conn.execute(
        """
        SELECT
            MIN(date_path_resolved) as min_date,
            MAX(date_path_resolved) as max_date
        FROM files
        WHERE date_path_resolved IS NOT NULL
    """
    ).fetchone()

    if date_range["min_date"]:
        print(f"  Earliest date: {date_range['min_date']}")
        print(f"  Latest date:   {date_range['max_date']}")
        print(f"  Today:         {today_int}")

    _print_subsection("Suspicious Dates")

    # Before 2000
    old_dates = conn.execute(
        """
        SELECT COUNT(*) FROM files
        WHERE date_path_resolved IS NOT NULL
          AND date_path_resolved < 20000101
    """
    ).fetchone()[0]

    # Future dates
    future_dates = conn.execute(
        f"""
        SELECT COUNT(*) FROM files
        WHERE date_path_resolved IS NOT NULL
          AND date_path_resolved > {today_int}
    """
    ).fetchone()[0]

    print(f"  Dates before 2000: {old_dates:,}")
    print(f"  Future dates:      {future_dates:,}")

    # Sample old dates
    if old_dates > 0:
        old_samples = conn.execute(
            """
            SELECT source_path, date_path_resolved, date_resolved_source
            FROM files
            WHERE date_path_resolved < 20000101
            LIMIT ?
        """,
            (config.sample_limit,),
        ).fetchall()

        print("\n  Sample pre-2000 dates:")
        for row in old_samples:
            print(f"    {row['date_path_resolved']}: {row['source_path']}")

    _print_subsection("Suspiciously Round Dates")

    round_dates = conn.execute(
        """
        SELECT date_path_resolved, COUNT(*) as cnt
        FROM files
        WHERE date_path_resolved IN (
            20000101, 20100101, 20101010, 20110101, 20111111,
            20120101, 20200101, 20200202, 20210101, 20220101
        )
        GROUP BY date_path_resolved
        ORDER BY cnt DESC
    """
    ).fetchall()

    if round_dates:
        print("  Potentially suspicious round dates:")
        for row in round_dates:
            print(f"    {row['date_path_resolved']}: {row['cnt']:,} files")
    else:
        print("  No suspicious round dates found.")

    _print_subsection("Year Distribution")

    year_dist = conn.execute(
        """
        SELECT
            CAST(date_path_resolved / 10000 AS INTEGER) as year,
            COUNT(*) as cnt
        FROM files
        WHERE date_path_resolved IS NOT NULL
        GROUP BY year
        ORDER BY year
    """
    ).fetchall()

    print("  Year  |  Count  | Bar")
    print("  ------+---------+----")
    max_cnt = max(row["cnt"] for row in year_dist) if year_dist else 1
    for row in year_dist:
        bar_len = int(row["cnt"] / max_cnt * 40)
        bar = "█" * bar_len
        print(f"  {row['year']}  | {row['cnt']:>7,} | {bar}")


def _analyze_folders(
    conn: sqlite3.Connection, config: AnalysisConfig  # pylint: disable=unused-argument
) -> None:
    """Analyze folder-level patterns for sibling inference planning."""
    _print_section("6. FOLDER-LEVEL ANALYSIS")
    print("  (This section has slow queries, please wait...)")

    # Extensions without dots (as stored in DB)
    image_exts = (
        "('jpg', 'jpeg', 'png', 'arw', 'nef', 'cr2', "
        "'dng', 'heic', 'tiff', 'tif', 'raw', 'raf', 'srw')"
    )

    _print_subsection("Folder Composition")

    print("  Counting folders...", end=" ")
    query = "SELECT COUNT(DISTINCT directory_path) FROM files"
    total_folders = conn.execute(query).fetchone()[0]
    print(f"done. Total: {total_folders:,}")

    # Folders with only images
    print("  Analyzing image-only folders...", end=" ")
    image_only = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT directory_path
            FROM files
            GROUP BY directory_path
            HAVING SUM(CASE WHEN LOWER(extension) IN {image_exts} THEN 1 ELSE 0 END) > 0
               AND SUM(CASE WHEN LOWER(extension) NOT IN {image_exts} THEN 1 ELSE 0 END) = 0
        )
    """
    ).fetchone()[0]
    print("done.")

    # Mixed folders (images + other)
    print("  Analyzing mixed folders...", end=" ")
    mixed = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT directory_path
            FROM files
            GROUP BY directory_path
            HAVING SUM(CASE WHEN LOWER(extension) IN {image_exts} THEN 1 ELSE 0 END) > 0
               AND SUM(CASE WHEN LOWER(extension) NOT IN {image_exts} THEN 1 ELSE 0 END) > 0
        )
    """
    ).fetchone()[0]
    print("done.")

    # Non-image only
    print("  Analyzing non-image folders...", end=" ")
    non_image_only = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT directory_path
            FROM files
            GROUP BY directory_path
            HAVING SUM(CASE WHEN LOWER(extension) IN {image_exts} THEN 1 ELSE 0 END) = 0
        )
    """
    ).fetchone()[0]
    print("done.")

    print(f"  Image-only folders:     {_pct(image_only, total_folders)}")
    print(f"  Mixed folders:          {_pct(mixed, total_folders)}")
    print(f"  Non-image-only folders: {_pct(non_image_only, total_folders)}")

    _print_subsection("Sibling Inference Viability")

    # Mixed folders where images have dates
    print("  Analyzing sibling inference viability...", end=" ")
    mixed_with_dated_images = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT directory_path
            FROM files
            GROUP BY directory_path
            HAVING SUM(CASE WHEN LOWER(extension) IN {image_exts} THEN 1 ELSE 0 END) > 0
               AND SUM(CASE WHEN LOWER(extension) NOT IN {image_exts} THEN 1 ELSE 0 END) > 0
               AND SUM(CASE WHEN LOWER(extension) IN {image_exts}
                            AND date_path_resolved IS NOT NULL THEN 1 ELSE 0 END) > 0
        )
    """
    ).fetchone()[0]
    print("done.")

    print(f"  Mixed folders with dated images: {_pct(mixed_with_dated_images, mixed)}")

    _print_subsection("Date Consistency Within Folders")

    # Folders where images have multiple different dates
    print("  Analyzing date consistency...", end=" ")
    inconsistent_folders = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT directory_path
            FROM files
            WHERE LOWER(extension) IN {image_exts}
              AND date_path_resolved IS NOT NULL
            GROUP BY directory_path
            HAVING COUNT(DISTINCT date_path_resolved) > 1
        )
    """
    ).fetchone()[0]
    print("done.")

    print("  Analyzing consistent folders...", end=" ")
    consistent_folders = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT directory_path
            FROM files
            WHERE LOWER(extension) IN {image_exts}
              AND date_path_resolved IS NOT NULL
            GROUP BY directory_path
            HAVING COUNT(DISTINCT date_path_resolved) = 1
        )
    """
    ).fetchone()[0]
    print("done.")

    total_dated = consistent_folders + inconsistent_folders
    print(f"  Folders with single date:    {_pct(consistent_folders, total_dated)}")
    print(f"  Folders with multiple dates: {_pct(inconsistent_folders, total_dated)}")


def _analyze_source_columns(conn: sqlite3.Connection, config: AnalysisConfig) -> None:
    """Inspect what the strategies actually matched on."""
    _print_section("7. SOURCE COLUMN INSPECTION")

    _print_subsection("Most Common Folder Date Sources")

    folder_sources = conn.execute(
        """
        SELECT date_path_folder_source, COUNT(*) as cnt
        FROM files
        WHERE date_path_folder_source IS NOT NULL
        GROUP BY date_path_folder_source
        ORDER BY cnt DESC
        LIMIT ?
    """,
        (config.top_n,),
    ).fetchall()

    if folder_sources:
        for row in folder_sources:
            print(f"  {row['cnt']:>8,}: {row['date_path_folder_source']}")
    else:
        print("  No folder sources found.")

    _print_subsection("Most Common Filename Patterns")

    # Group by pattern (extract prefix before date)
    filename_patterns = conn.execute(
        """
        SELECT
            CASE
                WHEN filename_full LIKE 'IMG_%' THEN 'IMG_*'
                WHEN filename_full LIKE 'DSC%' THEN 'DSC*'
                WHEN filename_full LIKE 'DSCF%' THEN 'DSCF*'
                WHEN filename_full LIKE 'DSCN%' THEN 'DSCN*'
                WHEN filename_full LIKE 'P%' THEN 'P*'
                WHEN filename_full LIKE '%-%' THEN '*-*'
                WHEN filename_full LIKE '%_%' THEN '*_*'
                ELSE 'other'
            END as pattern,
            COUNT(*) as cnt
        FROM files
        WHERE date_path_filename IS NOT NULL
        GROUP BY pattern
        ORDER BY cnt DESC
    """
    ).fetchall()

    if filename_patterns:
        for row in filename_patterns:
            print(f"  {row['cnt']:>8,}: {row['pattern']}")

    _print_subsection("Sample Filename Sources")

    filename_samples = conn.execute(
        """
        SELECT date_path_filename_source, COUNT(*) as cnt
        FROM files
        WHERE date_path_filename_source IS NOT NULL
        GROUP BY date_path_filename_source
        ORDER BY cnt DESC
        LIMIT ?
    """,
        (config.top_n,),
    ).fetchall()

    if filename_samples:
        for row in filename_samples:
            print(f"  {row['cnt']:>8,}: {row['date_path_filename_source']}")

    _print_subsection("Hierarchy Source Patterns")

    hierarchy_samples = conn.execute(
        """
        SELECT date_path_hierarchy_source, COUNT(*) as cnt
        FROM files
        WHERE date_path_hierarchy_source IS NOT NULL
        GROUP BY date_path_hierarchy_source
        ORDER BY cnt DESC
        LIMIT ?
    """,
        (config.top_n,),
    ).fetchall()

    if hierarchy_samples:
        for row in hierarchy_samples:
            print(f"  {row['cnt']:>8,}: {row['date_path_hierarchy_source']}")
    else:
        print("  No hierarchy sources found.")
        print("  (Hierarchy requires yyyy/mm/dd folder structure, e.g., 2024/05/14/)")

    print()
    print("=" * 80)
    print("END OF REPORT")
    print("=" * 80)
