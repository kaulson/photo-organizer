"""Configuration module for photosort."""

from dataclasses import dataclass, field
from pathlib import Path


def _get_project_root() -> Path:
    return Path(__file__).parent.parent


@dataclass
class ScannerConfig:
    progress_interval: int = 1000
    max_path_length: int = 4096


@dataclass
class Config:
    database_path: Path = field(default_factory=lambda: _get_project_root() / "data" / "catalog.db")
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
