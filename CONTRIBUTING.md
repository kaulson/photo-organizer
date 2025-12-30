# Contributing Guidelines

This document captures the code style, testing procedures, and design principles for this project.

## Code Style

### General Principles

- **PEP 8 compliant**: Follow Python's style guide
- **Type hints everywhere**: All function signatures must have type hints
- **Minimal comments**: Code should be self-documenting through clear naming
- **Small functions**: Single responsibility, easy to test independently
- **Simple patterns**: Only introduce abstractions when necessary
- **Optimize for debuggability**: Favor explicitness over cleverness

### Naming Conventions

- **Functions**: `snake_case`, verb-based (e.g., `parse_filename`, `get_drive_uuid`)
- **Classes**: `PascalCase`, noun-based (e.g., `Scanner`, `ProgressReporter`)
- **Constants**: `UPPER_SNAKE_CASE`
- **Private functions**: Prefix with underscore (e.g., `_get_device_for_mount`)

### Type Hints

```python
# Good
def parse_filename(filename: str) -> ParsedFilename:
    ...

def get_drive_uuid(mount_point: str | Path) -> str:
    ...

# Use | for unions (Python 3.10+)
def process(value: int | None) -> str:
    ...
```

### Docstrings

- **Modules**: Required, single line describing purpose
- **Classes**: Required, single line describing purpose
- **Public functions**: Optional if name is self-explanatory
- **Private functions**: Not required

```python
"""Database connection management."""

class Database:
    """SQLite database connection wrapper with context manager support."""
    ...
```

### Logging

Use lazy `%` formatting, not f-strings:

```python
# Good
logger.warning("Permission denied: %s", path)

# Bad
logger.warning(f"Permission denied: {path}")
```

### Quote Style

Use double quotes `"` consistently throughout the codebase.

### Modern Python Features

- Use `dataclasses` for data containers
- Use `Enum` for finite sets of values
- Use `Path` from `pathlib` for file paths
- Use `collections.abc.Iterator` instead of `typing.Iterator`

## Project Structure

```
photosort/
├── __init__.py           # Package exports
├── cli.py                # Click-based CLI
├── config.py             # Configuration dataclasses
├── database/
│   ├── __init__.py       # Database module exports
│   ├── connection.py     # Database class
│   ├── models.py         # Dataclasses for DB records
│   └── schema.py         # SQL schema definition
└── scanner/
    ├── __init__.py       # Scanner module exports
    ├── filesystem.py     # Directory walking utilities
    ├── progress.py       # Progress reporting
    ├── scanner.py        # Main Scanner class
    └── uuid.py           # Drive UUID detection
```

## Testing & Code Quality

### Running All Checks

```bash
make check
```

This runs pre-commit hooks including:
- `black` - Code formatting
- `flake8` - Linting
- `pylint` - Static analysis
- `mypy` - Type checking

### Running Tests

```bash
# All tests
uv run pytest tests/ -v

# Specific test file
uv run pytest tests/test_scanner.py -v

# Specific test
uv run pytest tests/test_scanner.py::TestScanner::test_scan_with_files -v
```

### Test Structure

- Tests live in `tests/` directory
- Test files mirror source structure: `test_<module>.py`
- Use `pytest` fixtures (`tmp_path`, `monkeypatch`)
- Each test class needs a docstring

```python
"""Tests for scanner module."""

class TestWalkDirectory:
    """Tests for walk_directory function."""

    def test_walks_empty_directory(self, tmp_path: Path) -> None:
        ...
```

### Database Inspection

Start Datasette to visually inspect the database:

```bash
docker compose up -d
# Open http://localhost:8001
```

## Design Principles

### SOLID but Pragmatic

- **Single Responsibility**: Each module/function does one thing
- **Open for extension**: Use composition over inheritance
- **Don't over-engineer**: Only add abstraction when needed

### Testability

- Each component should be testable in isolation
- Use dependency injection (pass `Database` to `Scanner`)
- Mock external dependencies in tests (`monkeypatch`)

### Explorability

The codebase is designed for exploring real-world data:
- Simple, flat structure reduces cognitive load
- Database stores all metadata for SQL exploration
- Datasette provides visual inspection

### Error Handling

- Log warnings/errors but continue when possible
- Use specific exception classes (e.g., `DriveUUIDError`)
- Preserve progress on interruption

## CLI Commands

```bash
# Start a new scan
uv run photosort scan /path/to/source

# Resume interrupted scan (auto-detects path)
uv run photosort scan --resume

# Show scan status
uv run photosort status

# Get help
uv run photosort --help
uv run photosort scan --help
```

## Common Tasks

### Adding a New Module

1. Create the module file with docstring
2. Add exports to `__init__.py`
3. Create corresponding test file
4. Run `make check` to verify

### Fixing Lint Errors

```bash
# See what black would change
uv run black --check photosort/

# Auto-format
uv run black photosort/

# Check specific file with pylint
uv run pylint photosort/cli.py
```

### Database Location

The SQLite database is stored at `data/catalog.db` (gitignored).

## Configuration

### pyproject.toml Settings

- **Line length**: 100 characters
- **Python version**: 3.14+
- **mypy**: `ignore_missing_imports = true`

### Pre-commit Hooks

Configured in `.pre-commit-config.yaml`, run automatically via `make check`.
