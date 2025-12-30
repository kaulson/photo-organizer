"""CLI interface for photosort."""

import sys
from datetime import datetime
from pathlib import Path

import click

from photosort.analysis.cli import analyze
from photosort.config import Config
from photosort.database import Database
from photosort.resolver.resolver import DateResolver
from photosort.scanner import Scanner
from photosort.scanner.uuid import DriveUUIDError


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config()


cli.add_command(analyze)


@cli.command()
@click.argument("source_path", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--resume", is_flag=True, help="Resume an interrupted scan")
@click.option("--progress-interval", type=int, default=1000, help="Print status every N files")
@click.option("--database", type=click.Path(path_type=Path), help="Path to database file")
@click.pass_context
def scan(
    ctx: click.Context,
    source_path: Path | None,
    resume: bool,
    progress_interval: int,
    database: Path | None,
) -> None:
    config: Config = ctx.obj["config"]
    db_path = database or config.database_path

    if source_path is None:
        if not resume:
            click.echo("Error: SOURCE_PATH is required unless --resume is used.", err=True)
            sys.exit(1)
        source_path = _get_last_scan_path(db_path)
        if source_path is None:
            click.echo("Error: No interrupted scan found to resume.", err=True)
            sys.exit(1)
        click.echo(f"Resuming scan of: {source_path}")

    try:
        with Database(db_path) as db:
            scanner = Scanner(db, progress_interval=progress_interval)
            scanner.scan(source_path, resume=resume)
    except DriveUUIDError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


def _get_last_scan_path(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None

    with Database(db_path) as db:
        row = db.conn.execute(
            """
            SELECT source_root FROM scan_sessions
            WHERE status = 'running'
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()

        if row:
            return Path(row["source_root"])
    return None


@cli.command()
@click.option("--database", type=click.Path(path_type=Path), help="Path to database file")
@click.pass_context
def status(ctx: click.Context, database: Path | None) -> None:
    config: Config = ctx.obj["config"]
    db_path = database or config.database_path

    if not db_path.exists():
        click.echo("No database found. Run 'photosort scan' first.")
        return

    with Database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT source_root, status, files_scanned, total_bytes,
                   started_at, completed_at
            FROM scan_sessions
            ORDER BY started_at DESC
            """
        ).fetchall()

        if not rows:
            click.echo("No scan sessions found.")
            return

        click.echo("\nScan Sessions:")
        click.echo("-" * 80)
        header = "Source".ljust(35) + "Status".ljust(12)
        header += "Files".rjust(10) + "Size".rjust(12) + "Started".ljust(15)
        click.echo(header)
        click.echo("-" * 80)

        for row in rows:
            started = _format_relative_time(row["started_at"])
            size = _format_bytes(row["total_bytes"])
            source = _truncate(row["source_root"], 34)
            scan_status = row["status"]
            files = row["files_scanned"]
            click.echo(
                f"{source:<35} "
                f"{scan_status:<12} "
                f"{files:>10,} "
                f"{size:>12} "
                f"{started:<15}"
            )


def _format_relative_time(unix_timestamp: int | None) -> str:
    if not unix_timestamp:
        return "unknown"

    now = datetime.now()
    then = datetime.fromtimestamp(unix_timestamp)
    delta = now - then

    if delta.days > 1:
        return f"{delta.days} days ago"
    if delta.days == 1:
        return "yesterday"
    if delta.seconds > 3600:
        hours = delta.seconds // 3600
        return f"{hours}h ago"
    if delta.seconds > 60:
        minutes = delta.seconds // 60
        return f"{minutes}m ago"
    return "just now"


def _format_bytes(size: int | None) -> str:
    if size is None:
        return "0 B"
    size_f = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_f < 1024:
            return f"{size_f:.1f} {unit}"
        size_f /= 1024
    return f"{size_f:.1f} PB"


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return "..." + text[-(max_len - 3) :]


@cli.command("resolve-dates")
@click.option("--reprocess", is_flag=True, help="Reprocess all files, not just new ones")
@click.option("--batch-size", type=int, default=1000, help="Number of files per batch")
@click.option("--database", type=click.Path(path_type=Path), help="Path to database file")
@click.pass_context
def resolve_dates(
    ctx: click.Context,
    reprocess: bool,
    batch_size: int,
    database: Path | None,
) -> None:
    """Resolve dates for scanned files using path-based strategies."""
    config: Config = ctx.obj["config"]
    db_path = database or config.database_path

    if not db_path.exists():
        click.echo("Error: No database found. Run 'photosort scan' first.", err=True)
        sys.exit(1)

    with Database(db_path) as db:
        resolver = DateResolver(db, batch_size=batch_size)
        click.echo("Resolving dates...")
        stats = resolver.resolve_all(reprocess=reprocess)

    click.echo()
    click.echo("Date Resolution Complete:")
    click.echo(f"  Total files processed: {stats.total_files:,}")
    click.echo(f"  Files with hierarchy date: {stats.files_with_hierarchy:,}")
    click.echo(f"  Files with folder date: {stats.files_with_folder:,}")
    click.echo(f"  Files with filename date: {stats.files_with_filename:,}")
    click.echo(f"  Files resolved: {stats.files_resolved:,}")


def main() -> None:
    """Entry point for the CLI."""
    cli(standalone_mode=True)  # pylint: disable=no-value-for-parameter


if __name__ == "__main__":
    main()
