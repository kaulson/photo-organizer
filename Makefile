.PHONY: help format fix black flake8 pylint mypy lint check test clean pre-commit

# Default target
.DEFAULT_GOAL := help

help:  ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# Formatting commands (auto-fix)
format:  ## Auto-format all Python code with black
	uv run black photosort/ tests/

fix: format  ## Auto-fix all fixable issues (alias for format)
	@echo "✓ Auto-formatting complete"

# Individual linting tools (check only)
black:  ## Check code formatting with black (no changes)
	uv run black --check --diff photosort/ tests/

flake8:  ## Run flake8 linter
	uv run flake8 photosort/ tests/

pylint:  ## Run pylint linter
	uv run pylint photosort/ tests/

mypy:  ## Run mypy type checker
	uv run mypy photosort/ tests/

# Combined linting command
lint: black flake8 pylint mypy  ## Run all linting tools

# Check everything (like pre-commit)
check:  ## Run all checks (pre-commit hooks + linting)
	@echo "Running pre-commit hooks..."
	uv run pre-commit run --all-files
	@echo ""
	@echo "✓ All checks passed!"

# Testing
test:  ## Run pytest tests
	uv run pytest tests/ -v

test-cov:  ## Run tests with coverage report
	uv run pytest tests/ -v --cov=photosort --cov-report=term-missing --cov-report=html

# Pre-commit management
pre-commit:  ## Run pre-commit hooks on all files
	uv run pre-commit run --all-files

pre-commit-update:  ## Update pre-commit hooks to latest versions
	uv run pre-commit autoupdate

# Project management
clean:  ## Remove Python cache files and build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.coverage" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Cleaned up Python cache files"

install:  ## Sync dependencies with uv
	uv sync

install-hooks:  ## Install pre-commit hooks
	uv run pre-commit install

# Quick workflow commands
quick-check: format lint  ## Format code and run linters (fast feedback)
	@echo "✓ Quick check complete!"

ci: check test  ## Run all checks like CI would (pre-commit + tests)
	@echo "✓ CI checks complete!"
