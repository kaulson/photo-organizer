"""Sidecar file detection."""

# Sidecar file extensions
SIDECAR_EXTENSIONS: frozenset[str] = frozenset(
    {
        "xmp",  # Adobe XMP sidecar
        "thm",  # Thumbnail (Canon)
        "aae",  # Apple photo edits
        "json",  # Some apps use JSON sidecars
        "xml",  # Generic metadata sidecar
    }
)

# Image extensions that can have sidecars
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Common formats
        "jpg",
        "jpeg",
        "png",
        "gif",
        "bmp",
        "tiff",
        "tif",
        "webp",
        # RAW formats
        "arw",
        "nef",
        "cr2",
        "cr3",
        "dng",
        "orf",
        "raf",
        "rw2",
        "srw",
        "pef",
        # Apple formats
        "heic",
        "heif",
        # Others
        "psd",
        "psb",
        # Video formats (can also have sidecars like .thm)
        "mov",
        "mp4",
        "avi",
        "mkv",
        "m4v",
        "mts",
        "m2ts",
    }
)


def detect_sidecar(
    *,
    filename_base: str,
    extension: str | None,
    folder_files: list[dict],
) -> bool:
    """Detect if a file is a sidecar for another file.

    A file is a sidecar if:
    1. It has a sidecar extension (xmp, thm, aae, etc.)
    2. Another file in the same folder has the same base name
    3. That other file is an image or video

    Args:
        filename_base: Base filename without extension.
        extension: File extension (without dot), or None.
        folder_files: List of dicts with keys:
            - 'filename_base': str
            - 'extension': str | None

    Returns:
        True if file is a sidecar, False otherwise.
    """
    if extension is None:
        return False

    ext_lower = extension.lower()

    # Must be a sidecar extension
    if ext_lower not in SIDECAR_EXTENSIONS:
        return False

    # Look for matching image/video file
    for other in folder_files:
        # Skip self
        if (
            other["filename_base"] == filename_base
            and other.get("extension", "").lower() == ext_lower
        ):
            continue

        # Check for matching base name with image/video extension
        if other["filename_base"] == filename_base:
            other_ext = (other.get("extension") or "").lower()
            if other_ext in IMAGE_EXTENSIONS:
                return True

    return False
