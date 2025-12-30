"""Progress reporting utilities for scanning."""

import sys
import time
from dataclasses import dataclass, field


@dataclass
class ScanStats:
    """Statistics for an ongoing scan operation."""

    files_scanned: int = 0
    directories_scanned: int = 0
    total_bytes: int = 0
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time


class ProgressReporter:
    """Reports scan progress to the user."""

    def __init__(self, interval: int = 1000):
        self.interval = interval
        self._last_report_count = 0

    def report_if_needed(self, stats: ScanStats, current_directory: str) -> None:
        if stats.files_scanned - self._last_report_count >= self.interval:
            self._print_progress(stats, current_directory)
            self._last_report_count = stats.files_scanned

    def report_completion(self, stats: ScanStats) -> None:
        duration = _format_duration(stats.elapsed_seconds)
        print(
            f"\nScan complete: {stats.files_scanned:,} files in "
            f"{stats.directories_scanned:,} directories ({duration})"
        )
        print(f"Total size: {_format_bytes(stats.total_bytes)}")

    def report_interruption(self, stats: ScanStats) -> None:
        print(
            f"\nScan interrupted. Progress saved. Run with --resume to continue.\n"
            f"Scanned: {stats.files_scanned:,} files in "
            f"{stats.directories_scanned:,} directories"
        )

    def report_resume(self, files: int, directories: int) -> None:
        print(f"Previous progress: {files:,} files in {directories:,} directories")
        print(f"Skipping {directories:,} completed directories...")

    def _print_progress(self, stats: ScanStats, current_directory: str) -> None:
        display_dir = current_directory if current_directory else "/"
        print(f"[{stats.files_scanned:,} files] Scanning: {display_dir}/", file=sys.stderr)


def _format_duration(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_bytes(size: int) -> str:
    size_f = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_f < 1024:
            return f"{size_f:.2f} {unit}"
        size_f /= 1024
    return f"{size_f:.2f} PB"
