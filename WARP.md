# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

This is a Python-based LaTeX API service. The project uses Python 3.13 and the `uv` package manager for dependency management.

## Development Setup

### Virtual Environment

The project uses a Python virtual environment managed by `uv`:

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies
uv pip install -e .

# Add new dependencies
uv pip install <package-name>
```

### Running the Application

```bash
# Run the main application
python main.py

# Or with the virtual environment activated
.venv/bin/python main.py
```

## Project Structure

- `main.py` - Entry point for the application
- `pyproject.toml` - Project metadata and dependencies
- `.python-version` - Specifies Python 3.13
- `.venv/` - Virtual environment (git-ignored)

## Package Management

This project uses `uv` as the package manager. All dependencies should be added to `pyproject.toml` under the `dependencies` array.

**To add a dependency:**
```bash
uv pip install <package-name>
# Then manually add to pyproject.toml dependencies list
```

**To sync dependencies:**
```bash
uv pip install -e .
```

## Python Version

The project requires Python 3.13 as specified in `.python-version`. Ensure this version is installed before setting up the development environment.
