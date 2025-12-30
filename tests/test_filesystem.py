"""Tests for filesystem utilities."""

from photosort.database.models import ParsedFilename
from photosort.scanner.filesystem import parse_filename


class TestParseFilename:
    """Tests for parse_filename function."""

    def test_simple_extension(self):
        result = parse_filename("photo.JPG")
        assert result == ParsedFilename(full="photo.JPG", base="photo", extension="jpg")

    def test_double_extension(self):
        result = parse_filename("archive.tar.gz")
        assert result == ParsedFilename(full="archive.tar.gz", base="archive.tar", extension="gz")

    def test_no_extension(self):
        result = parse_filename("README")
        assert result == ParsedFilename(full="README", base="README", extension=None)

    def test_dotfile_no_extension(self):
        result = parse_filename(".gitignore")
        assert result == ParsedFilename(full=".gitignore", base=".gitignore", extension=None)

    def test_dotfile_with_extension(self):
        result = parse_filename(".config.yaml")
        assert result == ParsedFilename(full=".config.yaml", base=".config", extension="yaml")

    def test_trailing_dot(self):
        result = parse_filename("file.")
        assert result == ParsedFilename(full="file.", base="file", extension=None)

    def test_xmp_sidecar(self):
        result = parse_filename("photo.JPG.xmp")
        assert result == ParsedFilename(full="photo.JPG.xmp", base="photo.JPG", extension="xmp")

    def test_empty_string(self):
        result = parse_filename("")
        assert result == ParsedFilename(full="", base="", extension=None)

    def test_extension_lowercase(self):
        result = parse_filename("IMAGE.CR2")
        assert result == ParsedFilename(full="IMAGE.CR2", base="IMAGE", extension="cr2")

    def test_multiple_dots(self):
        result = parse_filename("my.photo.backup.jpg")
        assert result == ParsedFilename(
            full="my.photo.backup.jpg", base="my.photo.backup", extension="jpg"
        )
