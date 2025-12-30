"""CLI for running data analysis."""

import sys
from pathlib import Path

import click

from photosort.analysis import AnalysisConfig, run_full_analysis
from photosort.config import Config


@click.command("analyze")
@click.option("--sample-limit", type=int, default=10, help="Number of samples to show")
@click.option("--top-n", type=int, default=20, help="Number of top items to show")
@click.option("--database", type=click.Path(path_type=Path), help="Path to database file")
@click.pass_context
def analyze(
    ctx: click.Context,
    sample_limit: int,
    top_n: int,
    database: Path | None,
) -> None:
    """Analyze date resolution patterns in the database.

    This command produces a comprehensive report covering:
    - Strategy coverage and effectiveness
    - Conflicts between strategies
    - Files without dates
    - Date sanity checks
    - Folder-level patterns for sibling inference
    """
    config_obj: Config = ctx.obj["config"]
    db_path = database or config_obj.database_path

    if not db_path.exists():
        click.echo("Error: No database found. Run 'photosort scan' first.", err=True)
        sys.exit(1)

    analysis_config = AnalysisConfig(
        sample_limit=sample_limit,
        top_n=top_n,
    )

    run_full_analysis(db_path, analysis_config)
