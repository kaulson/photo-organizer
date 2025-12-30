"""Tests for date extraction strategies."""

from photosort.resolver import (
    DateExtraction,
    extract_filename_date,
    extract_folder_date,
    extract_hierarchy_date,
    is_valid_date,
    to_date_int,
)


class TestDateHelpers:
    """Tests for date helper functions."""

    def test_is_valid_date_valid(self) -> None:
        assert is_valid_date(2023, 5, 14) is True
        assert is_valid_date(2020, 2, 29) is True  # Leap year
        assert is_valid_date(1900, 1, 1) is True

    def test_is_valid_date_invalid(self) -> None:
        assert is_valid_date(2023, 2, 30) is False
        assert is_valid_date(2021, 2, 29) is False  # Not leap year
        assert is_valid_date(2023, 13, 1) is False
        assert is_valid_date(2023, 0, 1) is False

    def test_to_date_int(self) -> None:
        assert to_date_int(2023, 5, 14) == 20230514
        assert to_date_int(2000, 1, 1) == 20000101
        assert to_date_int(1999, 12, 31) == 19991231


class TestExtractHierarchyDate:
    """Tests for yyyy/mm/dd folder hierarchy extraction."""

    def test_basic_hierarchy(self) -> None:
        result = extract_hierarchy_date("2023/05/14/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "2023/05/14"

    def test_hierarchy_with_prefix(self) -> None:
        result = extract_hierarchy_date("photos/2023/05/14/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "2023/05/14"

    def test_hierarchy_with_long_prefix(self) -> None:
        result = extract_hierarchy_date("backups/camera/2023/05/14/IMG_001.jpg")
        assert result.date_int == 20230514
        assert result.source == "2023/05/14"

    def test_deepest_hierarchy_wins(self) -> None:
        # If there are multiple valid hierarchies, deepest should win
        result = extract_hierarchy_date("2020/01/01/2023/05/14/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "2023/05/14"

    def test_no_hierarchy(self) -> None:
        result = extract_hierarchy_date("photos/vacation/photo.jpg")
        assert result.date_int is None
        assert result.source is None

    def test_incomplete_hierarchy(self) -> None:
        result = extract_hierarchy_date("2023/05/photo.jpg")
        assert result.date_int is None

    def test_invalid_date_hierarchy(self) -> None:
        # Feb 30 is invalid
        result = extract_hierarchy_date("2023/02/30/photo.jpg")
        assert result.date_int is None

    def test_boundary_years(self) -> None:
        result = extract_hierarchy_date("1900/01/01/photo.jpg")
        assert result.date_int == 19000101

        result = extract_hierarchy_date("2099/12/31/photo.jpg")
        assert result.date_int == 20991231

    def test_year_outside_range(self) -> None:
        result = extract_hierarchy_date("1899/01/01/photo.jpg")
        assert result.date_int is None

        result = extract_hierarchy_date("2100/01/01/photo.jpg")
        assert result.date_int is None


class TestExtractFolderDate:
    """Tests for single folder date extraction."""

    def test_compact_date(self) -> None:
        result = extract_folder_date("photos/20230514/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "20230514"

    def test_hyphen_separated(self) -> None:
        result = extract_folder_date("photos/2023-05-14/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "2023-05-14"

    def test_underscore_separated(self) -> None:
        result = extract_folder_date("photos/2023_05_14/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "2023_05_14"

    def test_date_with_suffix(self) -> None:
        result = extract_folder_date("photos/20230514-sunset/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "20230514-sunset"

    def test_date_with_prefix(self) -> None:
        result = extract_folder_date("photos/sunset-20230514/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "sunset-20230514"

    def test_deepest_folder_wins(self) -> None:
        result = extract_folder_date("20200101/subfolder/20230514-event/photo.jpg")
        assert result.date_int == 20230514
        assert result.source == "20230514-event"

    def test_no_date_folder(self) -> None:
        result = extract_folder_date("photos/vacation/photo.jpg")
        assert result.date_int is None

    def test_invalid_date_folder(self) -> None:
        result = extract_folder_date("photos/20230230/photo.jpg")  # Feb 30
        assert result.date_int is None

    def test_file_only_no_folder(self) -> None:
        result = extract_folder_date("photo.jpg")
        assert result.date_int is None


class TestExtractFilenameDate:
    """Tests for filename date extraction."""

    def test_prefixed_date(self) -> None:
        result = extract_filename_date("IMG_20230514_143052.jpg")
        assert result.date_int == 20230514
        assert result.source == "IMG_20230514_143052.jpg"

    def test_leading_date(self) -> None:
        result = extract_filename_date("20230514_IMG_001.arw")
        assert result.date_int == 20230514

    def test_hyphen_date(self) -> None:
        result = extract_filename_date("photo_2023-05-14.jpg")
        assert result.date_int == 20230514

    def test_no_date(self) -> None:
        result = extract_filename_date("photo.jpg")
        assert result.date_int is None

    def test_invalid_date(self) -> None:
        result = extract_filename_date("IMG_20230230_143052.jpg")  # Feb 30
        assert result.date_int is None

    def test_leftmost_date_wins(self) -> None:
        # If multiple dates, leftmost should win
        result = extract_filename_date("20230514_copy_20200101.jpg")
        assert result.date_int == 20230514


class TestDateExtractionDataclass:
    """Tests for DateExtraction dataclass."""

    def test_none_values(self) -> None:
        result = DateExtraction(None, None)
        assert result.date_int is None
        assert result.source is None

    def test_with_values(self) -> None:
        result = DateExtraction(20230514, "2023/05/14")
        assert result.date_int == 20230514
        assert result.source == "2023/05/14"
