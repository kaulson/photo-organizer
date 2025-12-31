"""Debug utilities for MetadataExtractor."""

import sys
from pathlib import Path

import click

from photosort.config import Config
from photosort.database.connection import Database
from photosort.extractor.strategies import SUPPORTED_EXTENSIONS, get_strategy


def debug_extractor(db_path: Path, strategy_name: str = "selective") -> None:
    """Debug why metadata extraction might not be finding files."""
    click.echo("=" * 60)
    click.echo("METADATA EXTRACTOR DEBUG REPORT")
    click.echo("=" * 60)

    with Database(db_path) as db:
        conn = db.conn

        # 1. Check total files in database
        cursor = conn.execute("SELECT COUNT(*) FROM files")
        total_files = cursor.fetchone()[0]
        click.echo(f"\nTotal files in database: {total_files:,}")

        # 2. Check files already in file_metadata
        cursor = conn.execute("SELECT COUNT(*) FROM file_metadata")
        extracted = cursor.fetchone()[0]
        click.echo(f"Files already extracted: {extracted:,}")

        # 3. Show extensions in database
        click.echo("\n--- Extensions in database (top 20) ---")
        cursor = conn.execute(
            """
            SELECT extension, COUNT(*) as cnt
            FROM files
            GROUP BY extension
            ORDER BY cnt DESC
            LIMIT 20
        """
        )
        for row in cursor.fetchall():
            ext = row[0] if row[0] else "(none)"
            click.echo(f"  {ext}: {row[1]:,}")

        # 4. Check supported extensions format
        click.echo("\n--- Supported extensions check ---")
        click.echo(f"SUPPORTED_EXTENSIONS: {sorted(SUPPORTED_EXTENSIONS)}")

        # Check with dot prefix (how strategies query)
        extensions_with_dot = [f".{ext}" for ext in SUPPORTED_EXTENSIONS]
        placeholders = ",".join("?" for _ in extensions_with_dot)

        cursor = conn.execute(
            f"SELECT COUNT(*) FROM files WHERE extension IN ({placeholders})",
            extensions_with_dot,
        )
        count_with_dot = cursor.fetchone()[0]
        click.echo(f"Files matching extensions WITH dot prefix: {count_with_dot:,}")

        # Check without dot prefix
        extensions_no_dot = list(SUPPORTED_EXTENSIONS)
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM files WHERE extension IN ({placeholders})",
            extensions_no_dot,
        )
        count_no_dot = cursor.fetchone()[0]
        click.echo(f"Files matching extensions WITHOUT dot prefix: {count_no_dot:,}")

        # 5. Sample some actual extension values
        click.echo("\n--- Sample extension values for supported types ---")
        cursor = conn.execute(
            """
            SELECT DISTINCT extension
            FROM files
            WHERE LOWER(REPLACE(extension, '.', '')) IN ('jpg', 'jpeg', 'arw', 'nef', 'mp4', 'mov')
            LIMIT 10
        """
        )
        for row in cursor.fetchall():
            click.echo(f"  {row[0]!r}")

        # 5b. File size distribution for supported types
        click.echo("\n--- File size distribution (supported extensions) ---")
        size_ranges = [
            ("< 1KB", 0, 1024),
            ("1KB - 10KB", 1024, 10 * 1024),
            ("10KB - 100KB", 10 * 1024, 100 * 1024),
            ("100KB - 1MB", 100 * 1024, 1024 * 1024),
            ("1MB - 10MB", 1024 * 1024, 10 * 1024 * 1024),
            ("10MB - 100MB", 10 * 1024 * 1024, 100 * 1024 * 1024),
            ("> 100MB", 100 * 1024 * 1024, None),
        ]
        for label, min_size, max_size in size_ranges:
            if max_size:
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM files WHERE extension IN ({placeholders}) "
                    "AND size >= ? AND size < ?",
                    extensions_no_dot + [min_size, max_size],
                )
            else:
                cursor = conn.execute(
                    f"SELECT COUNT(*) FROM files WHERE extension IN ({placeholders}) "
                    "AND size >= ?",
                    extensions_no_dot + [min_size],
                )
            count = cursor.fetchone()[0]
            click.echo(f"  {label}: {count:,}")

        # 6. Test the actual strategy
        click.echo(f"\n--- Testing strategy: {strategy_name} ---")
        strat = get_strategy(strategy_name)
        file_ids = strat.get_file_ids(conn, limit=10)
        click.echo(f"Files returned by strategy (limit 10): {len(file_ids)}")

        if file_ids:
            click.echo("\nSample files that would be processed:")
            placeholders = ",".join("?" for _ in file_ids[:5])
            cursor = conn.execute(
                f"""
                SELECT id, extension, source_path
                FROM files
                WHERE id IN ({placeholders})
                """,
                file_ids[:5],
            )
            for row in cursor.fetchall():
                click.echo(f"  [{row[0]}] {row[1]}: {row[2][:80]}...")

        # 7. Check if selective strategy conditions are the issue
        if strategy_name == "selective":
            click.echo("\n--- Selective strategy condition check ---")
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM files
                WHERE date_path_folder IS NULL AND date_path_filename IS NULL
            """
            )
            dateless = cursor.fetchone()[0]
            click.echo(f"Files without path dates: {dateless:,}")

            # Check how many of those have supported extensions
            cursor = conn.execute(
                f"""
                SELECT COUNT(*) FROM files
                WHERE date_path_folder IS NULL
                  AND date_path_filename IS NULL
                  AND extension IN ({placeholders})
                """,
                extensions_with_dot,
            )
            dateless_supported = cursor.fetchone()[0]
            click.echo(
                f"Dateless files with supported extensions (dot prefix): {dateless_supported:,}"
            )

            cursor = conn.execute(
                f"""
                SELECT COUNT(*) FROM files
                WHERE date_path_folder IS NULL
                  AND date_path_filename IS NULL
                  AND extension IN ({placeholders})
                """,
                extensions_no_dot,
            )
            dateless_supported_no_dot = cursor.fetchone()[0]
            click.echo(
                f"Dateless files with supported extensions (no dot): {dateless_supported_no_dot:,}"
            )

    click.echo("\n" + "=" * 60)
    click.echo("END DEBUG REPORT")
    click.echo("=" * 60)


def debug_extraction_errors(db_path: Path, limit: int = 20) -> None:
    """Show extraction errors and skip reasons from file_metadata table."""
    click.echo("=" * 60)
    click.echo("EXTRACTION ERRORS & SKIPS DEBUG")
    click.echo("=" * 60)

    with Database(db_path) as db:
        conn = db.conn

        # Skip reasons summary
        cursor = conn.execute(
            """
            SELECT skip_reason, COUNT(*) as cnt
            FROM file_metadata
            WHERE skip_reason IS NOT NULL
            GROUP BY skip_reason
            ORDER BY cnt DESC
            LIMIT 20
        """
        )
        results = cursor.fetchall()
        if results:
            click.echo("\n--- Skip reason summary ---")
            for row in results:
                click.echo(f"  [{row[1]:,}] {row[0]}")

        # Error summary
        cursor = conn.execute(
            """
            SELECT extraction_error, COUNT(*) as cnt
            FROM file_metadata
            WHERE extraction_error IS NOT NULL
            GROUP BY extraction_error
            ORDER BY cnt DESC
            LIMIT 20
        """
        )
        results = cursor.fetchall()
        if results:
            click.echo("\n--- Error summary ---")
            for row in results:
                click.echo(f"  [{row[1]:,}] {row[0][:100]}")

        cursor = conn.execute(
            f"""
            SELECT f.source_path, s.source_root, m.extraction_error
            FROM file_metadata m
            JOIN files f ON f.id = m.file_id
            JOIN scan_sessions s ON f.scan_session_id = s.id
            WHERE m.extraction_error IS NOT NULL
            LIMIT {limit}
        """
        )
        results = cursor.fetchall()
        if results:
            click.echo(f"\n--- Sample errors with absolute paths (limit {limit}) ---")
            for row in results:
                source_root = row["source_root"]
                relative_path = row["source_path"]
                absolute_path = f"{source_root}/{relative_path}" if relative_path else source_root
                click.echo(f"  {absolute_path}")
                click.echo(f"    Error: {row[2]}")


@click.command("debug-extractor")
@click.option(
    "--strategy",
    type=click.Choice(["full", "selective"]),
    default="selective",
    help="Strategy to debug",
)
@click.option("--errors", "show_errors", is_flag=True, help="Show extraction errors")
@click.option("--database", type=click.Path(path_type=Path), help="Path to database file")
@click.pass_context
def debug_extractor_cmd(
    ctx: click.Context,
    strategy: str,
    show_errors: bool,
    database: Path | None,
) -> None:
    """Debug metadata extraction to find why files aren't being processed."""
    config_obj: Config = ctx.obj["config"]
    db_path = database or config_obj.database_path

    if not db_path.exists():
        click.echo("Error: No database found. Run 'photosort scan' first.", err=True)
        sys.exit(1)

    if show_errors:
        debug_extraction_errors(db_path)
    else:
        debug_extractor(db_path, strategy)
