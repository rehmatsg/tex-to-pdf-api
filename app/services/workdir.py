"""
Work directory management for the LaTeX compiler service.

Provides safe creation, file writing, and guaranteed cleanup of temporary
compilation work directories.
"""

import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def create_workdir() -> Path:
    """
    Create a new temporary work directory for a compilation job.

    Returns the Path to the created directory.
    """
    work_dir = Path(tempfile.mkdtemp(prefix="latex_job_"))
    return work_dir


def cleanup_workdir(work_dir: Path) -> None:
    """
    Recursively delete a work directory.

    Never raises -- any errors during cleanup are logged and swallowed.
    Safe to call with a non-existent path.
    """
    try:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
    except Exception as exc:
        logger.warning("Failed to clean up work directory %s: %s", work_dir, exc)


def safe_write_file(work_dir: Path, relative_path: str, content: bytes) -> Path:
    """
    Write a file into the work directory at the given relative path.

    - Creates parent directories as needed.
    - Validates that the resolved destination stays within work_dir
      (prevents symlink escapes).

    Returns the absolute path to the written file.
    Raises ValueError if the resolved path escapes work_dir.
    """
    dest = (work_dir / relative_path).resolve()
    work_dir_resolved = work_dir.resolve()

    # Ensure destination is inside work_dir
    if (
        not str(dest).startswith(str(work_dir_resolved) + "/")
        and dest != work_dir_resolved
    ):
        raise ValueError(
            f"Resolved path {dest} is outside work directory {work_dir_resolved}"
        )

    # Create parent directories
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Write file -- use 'xb' (exclusive create) to avoid overwriting via race
    # If file already exists (duplicate path), overwrite is acceptable here
    dest.write_bytes(content)

    return dest
