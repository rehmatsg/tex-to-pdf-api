"""
Shared validation logic for the LaTeX compiler service.

Provides path validation, file extension whitelisting, dangerous macro scanning,
and resource limit enforcement. Used by both v1 and v2 endpoints.
"""

import os
from pathlib import PurePosixPath
from typing import Optional

from app.core.config import settings


# --- File extension whitelist ---

ALLOWED_EXTENSIONS: set[str] = {
    ".tex",
    ".bib",
    ".bst",
    ".cls",
    ".sty",  # LaTeX source files
    ".png",
    ".jpg",
    ".jpeg",
    ".pdf",  # Images
    ".txt",
    ".csv",  # Data files
    ".eps",
    ".svg",  # Additional image formats
}

SCANNABLE_EXTENSIONS: set[str] = {".tex", ".sty", ".cls"}

# --- Dangerous macros ---

DANGEROUS_MACROS: list[str] = [
    r"\write18",
    r"\immediate\write18",
    r"\input|",
    r"\openout",
    r"\openin",
    r"\newwrite",
    r"\newread",
]


class ValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, message: str, error_type: str = "invalid_input"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class PayloadTooLargeError(ValidationError):
    """Raised when upload exceeds size or count limits."""

    def __init__(self, message: str):
        super().__init__(message, error_type="payload_too_large")


def validate_file_path(path: str) -> str:
    """
    Validate that a file path is safe for use as a project-relative path.

    Rules:
    - Must not be empty
    - Must not contain null bytes
    - Must be relative (no leading /)
    - Must not contain '..' components
    - Must not contain backslashes
    - Must not exceed max path length
    - Must not start with a dot-hidden directory name

    Returns the normalized path string on success.
    Raises ValidationError on failure.
    """
    if not path or not path.strip():
        raise ValidationError("File path must not be empty")

    if "\x00" in path:
        raise ValidationError("File path must not contain null bytes")

    if "\\" in path:
        raise ValidationError(f"File path must not contain backslashes: {path!r}")

    if path.startswith("/"):
        raise ValidationError(f"File path must be relative, not absolute: {path!r}")

    if len(path) > settings.MAX_PATH_LENGTH:
        raise ValidationError(
            f"File path exceeds maximum length of {settings.MAX_PATH_LENGTH}: {path!r}"
        )

    # Normalize and check for traversal
    parts = PurePosixPath(path).parts
    for part in parts:
        if part == "..":
            raise ValidationError(f"File path must not contain '..': {path!r}")

    # Return the cleaned, normalized path
    normalized = str(PurePosixPath(path))
    return normalized


def validate_file_extension(filename: str) -> None:
    """
    Validate that a file has an allowed extension.

    Raises ValidationError if the extension is not in the whitelist.
    """
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        raise ValidationError(f"File has no extension: {filename!r}")
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"File type not allowed: {ext!r} (file: {filename!r})")


def scan_dangerous_macros(content: bytes, filename: str) -> None:
    """
    Scan file content for dangerous LaTeX macros.

    Only scans files with extensions in SCANNABLE_EXTENSIONS (.tex, .sty, .cls).
    For these file types, if the content cannot be decoded as UTF-8, the file
    is rejected as suspicious.

    Raises ValidationError if dangerous macros are found or content is suspicious.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SCANNABLE_EXTENSIONS:
        return

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError(
            f"File {filename!r} could not be decoded as UTF-8 and is suspicious "
            f"for a {ext} file"
        )

    for macro in DANGEROUS_MACROS:
        if macro in text:
            raise ValidationError(f"Dangerous macro detected in {filename!r}: {macro}")


def validate_limits(
    file_count: int,
    total_bytes: int,
    passes: int,
) -> None:
    """
    Enforce resource limits on compilation requests.

    Raises PayloadTooLargeError or ValidationError as appropriate.
    """
    if file_count > settings.MAX_FILE_COUNT:
        raise PayloadTooLargeError(
            f"Too many files: {file_count} (max {settings.MAX_FILE_COUNT})"
        )

    if total_bytes > settings.MAX_UPLOAD_SIZE:
        raise PayloadTooLargeError(
            f"Total upload size {total_bytes} bytes exceeds maximum "
            f"of {settings.MAX_UPLOAD_SIZE} bytes"
        )

    if not (1 <= passes <= settings.MAX_PASSES):
        raise ValidationError(
            f"Passes must be between 1 and {settings.MAX_PASSES}, got {passes}"
        )
