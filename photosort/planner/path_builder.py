"""Target path construction and duplicate handling."""

import hashlib
import re
from dataclasses import dataclass


MAX_ANNOTATION_LENGTH = 10


@dataclass
class DuplicateResult:
    """Result of checking for filename duplicates."""

    filename: str
    is_duplicate: bool
    source_hash: str | None = None


def build_target_folder(
    resolved_date: int,
    annotation: str | None,
) -> str:
    """Build target folder path from resolved date and optional annotation.

    Target structure: yyyy/yyyy_mm/yyyymmdd[-annotation]

    Args:
        resolved_date: Date as YYYYMMDD integer.
        annotation: Optional annotation to append (max 10 chars).

    Returns:
        Target folder path.
    """
    year = resolved_date // 10000
    month = (resolved_date // 100) % 100

    # Build folder name
    if annotation:
        folder_name = f"{resolved_date}-{annotation}"
    else:
        folder_name = str(resolved_date)

    return f"{year}/{year}_{month:02d}/{folder_name}"


def build_bucket_path(
    bucket: str,
    source_folder: str,
) -> str:
    """Build bucket path preserving original folder structure.

    Args:
        bucket: Bucket name ('mixed_dates' or 'non_media').
        source_folder: Original source folder path.

    Returns:
        Target path under bucket.
    """
    return f"_{bucket}/{source_folder}"


def extract_annotation(
    folder_name: str,
    resolved_date: int,
) -> str | None:
    """Extract annotation from folder name.

    Logic:
    1. If folder name starts with matching date prefix: strip date, return rest
    2. If folder name equals just the date: return None (no annotation)
    3. If folder name has no date prefix OR has a different date prefix:
       return the whole folder name as annotation

    Handles various date formats for stripping:
    - YYYYMMDD
    - YYYY_MM_DD
    - YYYY-MM-DD

    Args:
        folder_name: Original folder name.
        resolved_date: Resolved date as YYYYMMDD integer.

    Returns:
        Annotation string (max 10 chars) or None if no annotation.
    """
    # Build date patterns to check
    year = resolved_date // 10000
    month = (resolved_date // 100) % 100
    day = resolved_date % 100

    date_only_patterns = [
        rf"^{resolved_date}$",
        rf"^{year}_{month:02d}_{day:02d}$",
        rf"^{year}-{month:02d}-{day:02d}$",
    ]

    date_prefix_patterns = [
        # YYYYMMDD format with separator
        rf"^{resolved_date}[-_\s]+",
        # YYYY_MM_DD format with separator
        rf"^{year}_{month:02d}_{day:02d}[-_\s]+",
        # YYYY-MM-DD format with separator
        rf"^{year}-{month:02d}-{day:02d}[-_\s]+",
    ]

    # Check if folder name is just the date (no annotation)
    for pattern in date_only_patterns:
        if re.match(pattern, folder_name):
            return None

    # Check if folder name starts with the matching date prefix
    for pattern in date_prefix_patterns:
        match = re.match(pattern, folder_name)
        if match:
            # Found a matching date prefix - extract the rest as annotation
            annotation = folder_name[match.end() :]
            annotation = annotation.strip("-_ ")
            if annotation:
                if len(annotation) > MAX_ANNOTATION_LENGTH:
                    annotation = annotation[:MAX_ANNOTATION_LENGTH]
                return annotation
            return None

    # No matching date prefix found - use entire folder name as annotation
    annotation = folder_name.strip("-_ ")
    if not annotation:
        return None

    if len(annotation) > MAX_ANNOTATION_LENGTH:
        annotation = annotation[:MAX_ANNOTATION_LENGTH]

    return annotation


def resolve_filename_duplicate(
    filename: str,
    source_path: str,
    existing_filenames: set[str],
) -> DuplicateResult:
    """Resolve filename to handle potential duplicates.

    If filename already exists in target folder, generate a unique
    name using a hash of the source path.

    Args:
        filename: Original filename.
        source_path: Full source path (for hash generation).
        existing_filenames: Set of filenames already in target folder.

    Returns:
        DuplicateResult with final filename and duplicate flag.
    """
    if filename not in existing_filenames:
        return DuplicateResult(filename=filename, is_duplicate=False)

    # Generate hash from source path
    source_hash = _short_hash(source_path, length=6)

    # Split filename into name and extension
    name, ext = _split_extension(filename)

    # Build new filename
    new_filename = f"{name}_dupe_{source_hash}{ext}"

    return DuplicateResult(
        filename=new_filename,
        is_duplicate=True,
        source_hash=source_hash,
    )


def _short_hash(path: str, length: int = 6) -> str:
    """Generate short deterministic hash of a path.

    Args:
        path: Path string to hash.
        length: Number of hex characters to return.

    Returns:
        Short hash string.
    """
    full_hash = hashlib.sha256(path.encode()).hexdigest()
    return full_hash[:length]


def _split_extension(filename: str) -> tuple[str, str]:
    """Split filename into name and extension.

    Args:
        filename: Filename with or without extension.

    Returns:
        Tuple of (name, extension_with_dot).
        Extension includes the dot, or is empty string if none.
    """
    if "." not in filename:
        return filename, ""

    last_dot = filename.rfind(".")
    return filename[:last_dot], filename[last_dot:]
