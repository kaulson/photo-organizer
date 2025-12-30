"""Filesystem traversal utilities for scanning directories."""

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from photosort.database.models import ParsedFilename

logger = logging.getLogger(__name__)


@dataclass
class FileInfo:
    path: Path
    relative_path: str
    directory_path: str
    parsed_filename: ParsedFilename
    size: int
    stat_result: os.stat_result


@dataclass
class DirectoryBatch:
    directory_path: str
    files: list[FileInfo]


def parse_filename(filename: str) -> ParsedFilename:
    if not filename:
        return ParsedFilename(full=filename, base=filename, extension=None)

    dot_index = filename.rfind(".")

    if dot_index <= 0 or dot_index == len(filename) - 1:
        return ParsedFilename(full=filename, base=filename.rstrip("."), extension=None)

    extension = filename[dot_index + 1 :].lower()
    base = filename[:dot_index]

    return ParsedFilename(full=filename, base=base, extension=extension)


def walk_directory(
    source_root: Path,
    completed_dirs: set[str],
    max_path_length: int = 4096,
) -> Iterator[DirectoryBatch]:
    yield from _walk_recursive(
        current_dir=source_root,
        source_root=source_root,
        completed_dirs=completed_dirs,
        max_path_length=max_path_length,
    )


def _walk_recursive(
    current_dir: Path,
    source_root: Path,
    completed_dirs: set[str],
    max_path_length: int,
) -> Iterator[DirectoryBatch]:
    relative_dir = _get_relative_path(current_dir, source_root)

    if relative_dir in completed_dirs:
        logger.debug("Skipping completed directory: %s", relative_dir)
        subdirs = _list_subdirectories(current_dir)
        for subdir in subdirs:
            yield from _walk_recursive(subdir, source_root, completed_dirs, max_path_length)
        return

    files = _scan_directory_files(current_dir, source_root, max_path_length)
    subdirs = _list_subdirectories(current_dir)

    yield DirectoryBatch(directory_path=relative_dir, files=files)

    for subdir in subdirs:
        yield from _walk_recursive(subdir, source_root, completed_dirs, max_path_length)


def _get_relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
        return str(relative) if str(relative) != "." else ""
    except ValueError:
        return str(path)


def _list_subdirectories(directory: Path) -> list[Path]:
    subdirs: list[Path] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(Path(entry.path))
    except PermissionError:
        logger.warning("Permission denied listing directory: %s", directory)
    except OSError as e:
        logger.error("Error listing directory %s: %s", directory, e)
    return sorted(subdirs)


def _scan_directory_files(
    directory: Path,
    source_root: Path,
    max_path_length: int,
) -> list[FileInfo]:
    files: list[FileInfo] = []

    try:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                file_info = _process_entry(entry, source_root, max_path_length)
                if file_info:
                    files.append(file_info)
    except PermissionError:
        logger.warning("Permission denied scanning directory: %s", directory)
    except OSError as e:
        logger.error("Error scanning directory %s: %s", directory, e)

    return files


def _process_entry(
    entry: os.DirEntry,
    source_root: Path,
    max_path_length: int,
) -> FileInfo | None:
    try:
        if entry.is_symlink():
            return None

        if not entry.is_file(follow_symlinks=False):
            return None

        if len(entry.path) > max_path_length:
            logger.warning("Path too long, skipping: %s", entry.path)
            return None

        stat_result = entry.stat(follow_symlinks=False)
        path = Path(entry.path)
        relative_path = _get_relative_path(path, source_root)
        directory_path = _get_relative_path(path.parent, source_root)

        return FileInfo(
            path=path,
            relative_path=relative_path,
            directory_path=directory_path,
            parsed_filename=parse_filename(entry.name),
            size=stat_result.st_size,
            stat_result=stat_result,
        )

    except PermissionError:
        logger.warning("Permission denied: %s", entry.path)
        return None
    except FileNotFoundError:
        logger.warning("File disappeared during scan: %s", entry.path)
        return None
    except OSError as e:
        logger.error("Error processing %s: %s", entry.path, e)
        return None
