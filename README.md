# Photo Organizer

A Python-based tool for consolidating, classifying, deduplicating, and organizing ~6TB+ of photography and personal files scattered across multiple drives into a unified, well-structured archive.

## Overview

Photo Organizer follows a multi-phase pipeline approach:

1. **Scanner** - Walk filesystem and collect metadata into SQLite database
2. **Classifier** - Determine file types (camera RAW, phone photo, screenshot, etc.)
3. **Planner** - Generate target paths based on classification and dates
4. **Executor** - Perform actual file copies with verification

See [Architecture Documentation](docs/architecture/architecture.md) for detailed design.

## Requirements

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer and resolver
- exiftool (for metadata extraction)

## Development Setup

### Initial Setup

This project uses **`uv`** for Python environment and dependency management. Always use `uv sync` to manage dependencies, not `uv pip install`.

1. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd photo-organizer
   ```

3. **Create virtual environment and install dependencies**:
   ```bash
   uv sync
   ```
   This creates a `.venv/` directory with Python 3.14 and installs all dependencies in editable mode.

4. **Install pre-commit hooks**:
   ```bash
   make install-hooks
   # or manually: uv run pre-commit install
   ```

### Important: Use `uv sync`

**Always use `uv sync` to manage dependencies, not `uv pip install`.**

- `uv sync` - Syncs project dependencies from `pyproject.toml` and `uv.lock`
- Ensures reproducible environments across all development machines
- Automatically installs the project in editable mode
- Handles both regular and development dependencies

To add new dependencies:
```bash
# Edit pyproject.toml to add the dependency
# Then sync:
uv sync
```

### Running Commands

Use `uv run` to execute commands within the virtual environment:

```bash
uv run python script.py
uv run pytest
uv run black photosort/
```

Or use the Makefile commands (which use `uv run` internally):
```bash
make format
make lint
make test
```

## Code Quality & Style

### Tools

This project uses multiple tools to maintain code quality:

- **[Black](https://black.readthedocs.io/)** - Code formatter (100 char line length)
- **[Flake8](https://flake8.pycqa.org/)** - Linting (PEP 8 compliance)
- **[Pylint](https://pylint.pycqa.org/)** - Advanced linting (Google style guide based)
- **[mypy](https://mypy-lang.org/)** - Static type checking
- **[pre-commit](https://pre-commit.com/)** - Git hooks to run checks automatically

### Configuration

All tools are configured for consistency:

- **Line length**: 100 characters (all tools)
- **Python version**: 3.14 (runtime), 3.13 (Black target - closest supported version)
- **Style guide**: Google Python Style Guide (via `pylintrc`)
- **Compatibility**: Black and Flake8 are configured to work together (ignoring E203, W503)

Configuration files:
- [pyproject.toml](pyproject.toml) - Black, mypy, and project config
- [.flake8](.flake8) - Flake8 configuration
- [pylintrc](pylintrc) - Pylint configuration (Google style guide)
- [.pre-commit-config.yaml](.pre-commit-config.yaml) - Pre-commit hooks

### Makefile Commands

The `Makefile` provides convenient commands for development:

#### Auto-formatting
```bash
make format          # Auto-format all Python code with Black
make fix             # Alias for format
```

#### Individual Linting
```bash
make black           # Check formatting (no changes)
make flake8          # Run flake8 linter
make pylint          # Run pylint linter
make mypy            # Run mypy type checker
```

#### Combined Checks
```bash
make lint            # Run all linters (black, flake8, pylint, mypy)
make check           # Run all pre-commit hooks
make quick-check     # Format + lint (fast feedback)
make ci              # Full CI check (pre-commit + tests)
```

#### Testing
```bash
make test            # Run pytest tests
make test-cov        # Run tests with coverage report
```

#### Project Management
```bash
make clean           # Remove Python cache files
make install         # Sync dependencies with uv
make install-hooks   # Install pre-commit hooks
make help            # Show all available commands
```

### Development Workflow

**Recommended workflow before committing:**

```bash
# 1. Make your changes
# 2. Auto-format the code
make format

# 3. Run linters to catch issues
make lint

# 4. Or combine both steps:
make quick-check

# 5. Run tests
make test

# 6. Commit (pre-commit hooks will run automatically)
git commit
```

**If pre-commit hooks fail:**

```bash
# Auto-fix what can be fixed
make format

# Check specific linters
make flake8     # Check PEP 8 violations
make pylint     # Check Google style guide violations
make mypy       # Check type issues

# Run all checks again
make check
```

### Pre-commit Hooks

Pre-commit hooks run automatically on `git commit` and perform:

1. Basic checks (trailing whitespace, end of files, etc.)
2. Black formatting
3. Flake8 linting
4. Pylint linting
5. Mypy type checking

To run hooks manually on all files:
```bash
make pre-commit
# or: uv run pre-commit run --all-files
```

To update hook versions:
```bash
make pre-commit-update
```

### Style Guide Notes

- **Line length**: 100 characters (more readable for modern displays)
- **Docstrings**: Required for modules, classes, and public methods (12+ lines)
- **Type hints**: Required for all function signatures
- **Imports**: Sorted and organized (handled by linters)
- **Naming**: Follow PEP 8 (snake_case for functions/variables, PascalCase for classes)

## Project Structure

```
photo-organizer/
├── photosort/              # Main package
│   ├── __init__.py
│   ├── cli.py             # CLI entry points
│   ├── config.py          # Configuration management
│   ├── database/          # Database layer
│   └── scanner/           # Scanner component
├── tests/                  # Test suite
├── docs/                   # Documentation
│   └── architecture/      # Architecture specs
├── pyproject.toml         # Project configuration
├── Makefile               # Development commands
└── README.md              # This file
```

## Installation (End Users)

_Coming soon - when ready for distribution_

```bash
uv pip install photo-organizer
```

## Usage

_Coming soon - scanner implementation in progress_

```bash
# Scan a source drive
photosort scan /mnt/source_drive

# Show scan status
photosort status

# Classify files
photosort classify

# Generate and preview plan
photosort plan
photosort preview

# Execute the plan
photosort execute
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes following the code quality guidelines
4. Run `make quick-check` to ensure code quality
5. Run `make test` to ensure tests pass
6. Submit a pull request

## License

_To be determined_
