"""Database connection management."""

import sqlite3
from pathlib import Path
from typing import Self

from .schema import create_schema


class Database:
    """SQLite database connection wrapper with context manager support."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            create_schema(self._conn)
        return self._conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self.connect()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
