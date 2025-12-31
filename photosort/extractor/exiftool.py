"""Exiftool wrapper for metadata extraction."""

import json
import shutil
import subprocess
from dataclasses import dataclass


class ExiftoolNotFoundError(Exception):
    """Raised when exiftool is not installed."""


@dataclass
class ExiftoolResult:
    """Result from exiftool extraction."""

    source_file: str
    metadata: dict
    error: str | None = None


class ExiftoolRunner:
    """Wrapper for exiftool command execution."""

    EXIFTOOL_ARGS = ["-json", "-struct", "-G0", "-n", "-c", "%.6f"]

    def __init__(self) -> None:
        self.version = self._check_exiftool()

    def _check_exiftool(self) -> str:
        path = shutil.which("exiftool")
        if not path:
            raise ExiftoolNotFoundError(
                "exiftool is required but not found.\n"
                "Please install exiftool: https://exiftool.org/install.html"
            )

        result = subprocess.run(
            ["exiftool", "-ver"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def extract_batch(self, file_paths: list[str]) -> list[ExiftoolResult]:
        """Extract metadata from multiple files in a single exiftool call."""
        if not file_paths:
            return []

        cmd = ["exiftool"] + self.EXIFTOOL_ARGS + file_paths

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as e:
            return [ExiftoolResult(fp, {}, str(e)) for fp in file_paths]

        if result.returncode not in (0, 1):
            return [ExiftoolResult(fp, {}, result.stderr) for fp in file_paths]

        try:
            data_list = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError as e:
            return [ExiftoolResult(fp, {}, f"JSON parse error: {e}") for fp in file_paths]

        results = []
        data_by_source = {d.get("SourceFile", ""): d for d in data_list}

        for fp in file_paths:
            if fp in data_by_source:
                results.append(ExiftoolResult(fp, data_by_source[fp]))
            else:
                results.append(ExiftoolResult(fp, {}, "No output from exiftool"))

        return results

    def extract_single(self, file_path: str) -> ExiftoolResult:
        """Extract metadata from a single file."""
        results = self.extract_batch([file_path])
        return results[0] if results else ExiftoolResult(file_path, {}, "No result")
