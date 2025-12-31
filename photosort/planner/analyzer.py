"""Folder analysis for date statistics calculation."""

from dataclasses import dataclass

# Image extensions for classification (photos and RAW formats)
# Videos are NOT included - folder analysis focuses on images
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
    }
)


def is_image_extension(extension: str | None) -> bool:
    """Check if an extension is an image file type.

    Args:
        extension: File extension (without dot), or None.

    Returns:
        True if the extension is an image type, False otherwise.
    """
    if extension is None:
        return False
    return extension.lower() in IMAGE_EXTENSIONS


@dataclass
class FolderDateAnalysis:
    """Statistical analysis of dates within a folder.

    This captures all metrics needed to apply folder resolution rules.
    """

    # File counts
    total_files: int
    image_files: int
    images_with_date: int

    # Coverage
    date_coverage_pct: float  # images_with_date / image_files (0.0 if no images)

    # Prevalent date (most common)
    prevalent_date: int | None  # YYYYMMDD or None
    prevalent_date_count: int
    prevalent_date_pct: float  # prevalent_count / images_with_date (0.0 if none)

    # Date range
    min_date: int | None  # YYYYMMDD
    max_date: int | None  # YYYYMMDD
    date_span_months: int  # Calendar months between min and max

    # Uniqueness
    unique_date_count: int


def analyze_folder(files_data: list[dict]) -> FolderDateAnalysis:
    """Analyze a folder's files to compute date statistics.

    Args:
        files_data: List of dicts with keys:
            - 'date': int (YYYYMMDD) or None
            - 'is_image': bool

    Returns:
        FolderDateAnalysis with computed statistics.
    """
    total_files = len(files_data)
    image_files = sum(1 for f in files_data if f["is_image"])
    images_with_date = sum(1 for f in files_data if f["is_image"] and f["date"] is not None)

    # Coverage
    date_coverage_pct = (images_with_date / image_files) if image_files > 0 else 0.0

    # Collect dates from images only
    image_dates = [f["date"] for f in files_data if f["is_image"] and f["date"] is not None]

    if not image_dates:
        return FolderDateAnalysis(
            total_files=total_files,
            image_files=image_files,
            images_with_date=0,
            date_coverage_pct=date_coverage_pct,
            prevalent_date=None,
            prevalent_date_count=0,
            prevalent_date_pct=0.0,
            min_date=None,
            max_date=None,
            date_span_months=0,
            unique_date_count=0,
        )

    # Count date occurrences
    date_counts: dict[int, int] = {}
    for date in image_dates:
        date_counts[date] = date_counts.get(date, 0) + 1

    # Find prevalent date
    prevalent_date = max(date_counts, key=lambda d: date_counts[d])
    prevalent_date_count = date_counts[prevalent_date]
    prevalent_date_pct = prevalent_date_count / images_with_date

    # Date range
    min_date = min(image_dates)
    max_date = max(image_dates)
    date_span_months = _calculate_month_span(min_date, max_date)

    # Unique dates
    unique_date_count = len(date_counts)

    return FolderDateAnalysis(
        total_files=total_files,
        image_files=image_files,
        images_with_date=images_with_date,
        date_coverage_pct=date_coverage_pct,
        prevalent_date=prevalent_date,
        prevalent_date_count=prevalent_date_count,
        prevalent_date_pct=prevalent_date_pct,
        min_date=min_date,
        max_date=max_date,
        date_span_months=date_span_months,
        unique_date_count=unique_date_count,
    )


def _calculate_month_span(min_date: int, max_date: int) -> int:
    """Calculate the span in calendar months between two YYYYMMDD dates.

    Args:
        min_date: Start date as YYYYMMDD integer.
        max_date: End date as YYYYMMDD integer.

    Returns:
        Number of months between the dates (0 if same month).
    """
    min_year = min_date // 10000
    min_month = (min_date // 100) % 100
    max_year = max_date // 10000
    max_month = (max_date // 100) % 100

    return (max_year - min_year) * 12 + (max_month - min_month)
