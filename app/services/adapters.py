"""
Input adapters for the v2 compilation endpoints.

Each adapter takes raw input (multipart files or a zip archive) and populates
a work directory with validated, safe project files.  Both adapters share the
same validation rules from app.services.validators.
"""

import logging
import stat
import zipfile
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile

from app.core.config import settings
from app.services.validators import (
    PayloadTooLargeError,
    ValidationError,
    scan_dangerous_macros,
    validate_file_extension,
    validate_file_path,
    validate_limits,
)
from app.services.workdir import safe_write_file

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Multi-file adapter  (POST /v2/compile/sync)
# ---------------------------------------------------------------------------


async def build_workdir_from_multipart(
    files: list[UploadFile],
    work_dir: Path,
    passes: int,
) -> dict:
    """
    Populate *work_dir* from a list of multipart-uploaded files.

    Each file's ``filename`` header is treated as the project-relative path
    (e.g. ``src/main.tex``, ``figures/diagram.png``).

    Returns a metadata dict::

        {"file_count": int, "total_bytes": int}

    Raises:
        ValidationError  – on bad paths, disallowed extensions, dangerous macros
        PayloadTooLargeError – when limits are exceeded
    """
    if not files:
        raise ValidationError("No files provided")

    # Pre-validate file count + passes (total_bytes checked incrementally)
    validate_limits(file_count=len(files), total_bytes=0, passes=passes)

    total_bytes = 0

    for upload in files:
        # --- path safety ---
        raw_path = upload.filename
        if raw_path is None:
            raise ValidationError("Uploaded file is missing a filename")
        rel_path = validate_file_path(raw_path)

        # --- extension whitelist ---
        validate_file_extension(rel_path)

        # --- read content & enforce cumulative size ---
        content = await upload.read()
        total_bytes += len(content)

        if total_bytes > settings.MAX_UPLOAD_SIZE:
            raise PayloadTooLargeError(
                f"Total upload size exceeds {settings.MAX_UPLOAD_SIZE} bytes"
            )

        # --- dangerous macro scan (tex/sty/cls only) ---
        scan_dangerous_macros(content, rel_path)

        # --- write into work_dir ---
        safe_write_file(work_dir, rel_path, content)

    return {"file_count": len(files), "total_bytes": total_bytes}


# ---------------------------------------------------------------------------
# Zip adapter  (POST /v2/compile/zip)
# ---------------------------------------------------------------------------


def build_workdir_from_zip(
    zip_path: Path,
    work_dir: Path,
    passes: int,
) -> dict:
    """
    Extract a zip archive into *work_dir* with full security validation.

    Returns a metadata dict::

        {"file_count": int, "total_bytes": int}

    Raises:
        ValidationError  – on bad member paths, symlinks, disallowed extensions,
                           dangerous macros
        PayloadTooLargeError – when limits are exceeded
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()

        # --- validate member count ---
        # Filter out directory entries (they end with '/')
        file_members = [m for m in members if not m.filename.endswith("/")]
        validate_limits(file_count=len(file_members), total_bytes=0, passes=passes)

        total_bytes = 0

        for member in file_members:
            # --- reject symlinks ---
            # Unix symlinks in zip have the symlink bit set in external_attr
            unix_attrs = member.external_attr >> 16
            if unix_attrs and stat.S_ISLNK(unix_attrs):
                raise ValidationError(
                    f"Symlinks are not allowed in zip: {member.filename!r}"
                )

            # --- path safety ---
            rel_path = validate_file_path(member.filename)

            # --- extension whitelist ---
            validate_file_extension(rel_path)

            # --- enforce individual + cumulative size ---
            if member.file_size > settings.MAX_UPLOAD_SIZE:
                raise PayloadTooLargeError(
                    f"File {rel_path!r} uncompressed size "
                    f"({member.file_size} bytes) exceeds limit"
                )

            total_bytes += member.file_size
            if total_bytes > settings.MAX_UPLOAD_SIZE:
                raise PayloadTooLargeError(
                    f"Total uncompressed size exceeds {settings.MAX_UPLOAD_SIZE} bytes"
                )

            # --- extract content ---
            content = zf.read(member.filename)

            # --- dangerous macro scan ---
            scan_dangerous_macros(content, rel_path)

            # --- write into work_dir ---
            safe_write_file(work_dir, rel_path, content)

    return {"file_count": len(file_members), "total_bytes": total_bytes}
