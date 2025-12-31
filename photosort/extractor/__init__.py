"""Metadata extraction from image and video files."""

from photosort.extractor.exiftool import ExiftoolRunner, ExiftoolNotFoundError
from photosort.extractor.extractor import (
    MetadataExtractor,
    MetadataExtractorStats,
    MIN_FILE_SIZE_BYTES,
)
from photosort.extractor.strategies import (
    ExtractionStrategy,
    FullStrategy,
    SelectiveStrategy,
)

__all__ = [
    "ExiftoolRunner",
    "ExiftoolNotFoundError",
    "MetadataExtractor",
    "MetadataExtractorStats",
    "MIN_FILE_SIZE_BYTES",
    "ExtractionStrategy",
    "FullStrategy",
    "SelectiveStrategy",
]
