"""Tests for scanner module."""

from pathlib import Path

from photosort.database import Database
from photosort.scanner.filesystem import walk_directory
from photosort.scanner.scanner import Scanner


class TestWalkDirectory:
    """Tests for walk_directory function."""

    def test_walks_empty_directory(self, tmp_path: Path):
        batches = list(walk_directory(tmp_path, set()))
        assert len(batches) == 1
        assert batches[0].directory_path == ""
        assert batches[0].files == []

    def test_walks_single_file(self, tmp_path: Path):
        (tmp_path / "test.txt").write_text("hello")

        batches = list(walk_directory(tmp_path, set()))
        assert len(batches) == 1
        assert len(batches[0].files) == 1
        assert batches[0].files[0].parsed_filename.full == "test.txt"

    def test_walks_nested_directories(self, tmp_path: Path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "root.txt").write_text("root")
        (subdir / "nested.txt").write_text("nested")

        batches = list(walk_directory(tmp_path, set()))
        paths = {b.directory_path for b in batches}

        assert "" in paths
        assert "subdir" in paths

    def test_skips_completed_directories(self, tmp_path: Path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("test")

        completed = {"subdir"}
        batches = list(walk_directory(tmp_path, completed))

        dir_paths = [b.directory_path for b in batches]
        assert "subdir" not in dir_paths

    def test_skips_symlinks(self, tmp_path: Path):
        real_file = tmp_path / "real.txt"
        real_file.write_text("real")
        link = tmp_path / "link.txt"
        link.symlink_to(real_file)

        batches = list(walk_directory(tmp_path, set()))
        filenames = [f.parsed_filename.full for f in batches[0].files]

        assert "real.txt" in filenames
        assert "link.txt" not in filenames

    def test_includes_hidden_files(self, tmp_path: Path):
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("visible")

        batches = list(walk_directory(tmp_path, set()))
        filenames = [f.parsed_filename.full for f in batches[0].files]

        assert ".hidden" in filenames
        assert "visible.txt" in filenames

    def test_alphabetical_order(self, tmp_path: Path):
        (tmp_path / "zebra.txt").write_text("z")
        (tmp_path / "apple.txt").write_text("a")
        (tmp_path / "middle.txt").write_text("m")

        batches = list(walk_directory(tmp_path, set()))
        filenames = [f.parsed_filename.full for f in batches[0].files]

        assert filenames == ["apple.txt", "middle.txt", "zebra.txt"]


class TestScanner:
    """Tests for Scanner class."""

    def test_scan_empty_directory(self, tmp_path: Path, monkeypatch):
        db_path = tmp_path / "test.db"
        source = tmp_path / "source"
        source.mkdir()

        monkeypatch.setattr("photosort.scanner.scanner.get_drive_uuid", lambda _: "test-uuid-1234")

        with Database(db_path) as db:
            scanner = Scanner(db)
            stats = scanner.scan(source)

        assert stats.files_scanned == 0
        assert stats.directories_scanned == 1

    def test_scan_with_files(self, tmp_path: Path, monkeypatch):
        db_path = tmp_path / "test.db"
        source = tmp_path / "source"
        source.mkdir()
        (source / "file1.txt").write_text("content1")
        (source / "file2.txt").write_text("content2")

        monkeypatch.setattr("photosort.scanner.scanner.get_drive_uuid", lambda _: "test-uuid-1234")

        with Database(db_path) as db:
            scanner = Scanner(db)
            stats = scanner.scan(source)

        assert stats.files_scanned == 2

    def test_scan_creates_session(self, tmp_path: Path, monkeypatch):
        db_path = tmp_path / "test.db"
        source = tmp_path / "source"
        source.mkdir()

        monkeypatch.setattr("photosort.scanner.scanner.get_drive_uuid", lambda _: "test-uuid-1234")

        with Database(db_path) as db:
            scanner = Scanner(db)
            scanner.scan(source)

            row = db.conn.execute(
                "SELECT * FROM scan_sessions WHERE source_root = ?",
                (str(source),),
            ).fetchone()

            assert row is not None
            assert row["status"] == "completed"
            assert row["source_drive_uuid"] == "test-uuid-1234"

    def test_scan_stores_file_metadata(self, tmp_path: Path, monkeypatch):
        db_path = tmp_path / "test.db"
        source = tmp_path / "source"
        source.mkdir()
        test_file = source / "test.jpg"
        test_file.write_text("fake image content")

        monkeypatch.setattr("photosort.scanner.scanner.get_drive_uuid", lambda _: "test-uuid-1234")

        with Database(db_path) as db:
            scanner = Scanner(db)
            scanner.scan(source)

            row = db.conn.execute(
                "SELECT * FROM files WHERE filename_full = ?",
                ("test.jpg",),
            ).fetchone()

            assert row is not None
            assert row["extension"] == "jpg"
            assert row["filename_base"] == "test"
            assert row["size"] == len("fake image content")

    def test_rescan_overwrites_data(self, tmp_path: Path, monkeypatch):
        db_path = tmp_path / "test.db"
        source = tmp_path / "source"
        source.mkdir()
        (source / "file1.txt").write_text("v1")

        monkeypatch.setattr("photosort.scanner.scanner.get_drive_uuid", lambda _: "test-uuid-1234")

        with Database(db_path) as db:
            scanner = Scanner(db)
            scanner.scan(source)

            (source / "file2.txt").write_text("v2")
            scanner.scan(source)

            count = db.conn.execute(
                "SELECT COUNT(*) FROM scan_sessions WHERE source_root = ?",
                (str(source),),
            ).fetchone()[0]

            assert count == 1
