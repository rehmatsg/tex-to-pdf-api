"""
Core compilation pipeline for the LaTeX compiler service.

This module provides the single `compile_project()` function that all endpoints
(v1 and v2) funnel through. It handles:
- Main file verification
- Multi-pass pdflatex invocation with -no-shell-escape
- Output PDF detection based on actual main_file stem (fixes v1 bug)
- Log parsing for errors and warnings
- Log truncation
- Compile timeout handling
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.models.compile import CompileOptions, CompileResult

logger = logging.getLogger(__name__)


def compile_project(
    work_dir: Path,
    main_file: str,
    options: CompileOptions,
) -> CompileResult:
    """
    Compile a LaTeX project that has already been laid out in work_dir.

    Args:
        work_dir: Directory containing all project files.
        main_file: Relative path to the main .tex file within work_dir
                   (e.g. "main.tex" or "src/main.tex").
        options: Compilation options (engine, passes, timeout).

    Returns:
        CompileResult with success status, PDF path, timing, log, errors, warnings.

    This function does NOT create or clean up work_dir -- that is the caller's
    responsibility (via workdir.create_workdir / workdir.cleanup_workdir).
    """
    start_time = time.time()

    # --- Verify main file exists ---
    main_file_path = work_dir / main_file
    if not main_file_path.exists():
        return CompileResult(
            success=False,
            compile_time_ms=0,
            log=f"Main file '{main_file}' not found in work directory.",
            error_message=f"Main file not found: {main_file}",
            warnings=[],
            errors=[f"Main file not found: {main_file}"],
        )

    # --- Determine the directory to run pdflatex in and the relative filename ---
    # pdflatex must be invoked from the directory containing the main file's
    # parent so that \input / \include relative paths resolve correctly.
    # Example: main_file="src/main.tex" -> cwd=work_dir, arg="src/main.tex"
    compile_cwd = work_dir

    # --- Run compilation passes ---
    log_output = ""
    last_returncode: Optional[int] = None

    for i in range(options.passes):
        cmd = [
            settings.TEX_BIN_PATH,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "-no-shell-escape",
            main_file,
        ]

        try:
            result = subprocess.run(
                cmd,
                cwd=str(compile_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=options.timeout_seconds,
                text=True,
            )

            log_output += f"--- Pass {i + 1} ---\n"
            log_output += result.stdout or ""
            last_returncode = result.returncode

            if result.returncode != 0:
                break  # Stop on first error

        except FileNotFoundError:
            compile_time = int((time.time() - start_time) * 1000)
            return CompileResult(
                success=False,
                compile_time_ms=compile_time,
                log=log_output,
                error_message="pdflatex binary not found",
                warnings=[],
                errors=["pdflatex binary not found"],
            )

        except subprocess.TimeoutExpired:
            compile_time = int((time.time() - start_time) * 1000)
            log_output += (
                f"\n--- Timeout after {options.timeout_seconds}s on pass {i + 1} ---"
            )

            errors, warnings = _parse_log_messages(log_output)
            log_output, truncated = _truncate_log(log_output)

            return CompileResult(
                success=False,
                compile_time_ms=compile_time,
                log=log_output,
                error_message="Compilation timed out",
                log_truncated=truncated,
                warnings=warnings,
                errors=errors,
            )

    # --- Detect output PDF ---
    # pdflatex produces a PDF named after the main file's stem.  When run
    # from work_dir the output lands in the *cwd* (work_dir), NOT alongside
    # the source file.  E.g. running `pdflatex src/main.tex` from work_dir
    # creates `work_dir/main.pdf`, not `work_dir/src/main.pdf`.
    # We check the cwd location first, then the source directory as fallback.
    main_stem = Path(main_file).stem
    main_parent = Path(main_file).parent

    # Primary: PDF in cwd (work_dir)
    expected_pdf = work_dir / f"{main_stem}.pdf"
    if not expected_pdf.exists():
        # Fallback: PDF alongside source file
        expected_pdf = work_dir / main_parent / f"{main_stem}.pdf"

    success = expected_pdf.exists()

    # --- Parse and truncate log ---
    errors, warnings = _parse_log_messages(log_output)
    log_output, truncated = _truncate_log(log_output)

    compile_time = int((time.time() - start_time) * 1000)

    error_message: Optional[str] = None
    if not success:
        error_message = errors[0] if errors else "Compilation failed"

    return CompileResult(
        success=success,
        pdf_path=expected_pdf if success else None,
        compile_time_ms=compile_time,
        log=log_output,
        error_message=error_message,
        log_truncated=truncated,
        warnings=warnings,
        errors=errors,
    )


def _parse_log_messages(log: str) -> tuple[list[str], list[str]]:
    """
    Extract errors and warnings from LaTeX log output.

    Errors are detected in two formats:
    - Classic: lines starting with ``! `` (e.g. ``! Undefined control sequence.``)
    - File-line-error: ``./file.tex:42: Error message`` (from ``-file-line-error`` flag)

    Warnings: lines containing ``LaTeX Warning``.

    Returns (errors, warnings).
    """
    import re

    errors: list[str] = []
    warnings: list[str] = []
    # Pattern for -file-line-error format: ./file.tex:123: Error message
    file_line_re = re.compile(r"^\./[^:]+:\d+:\s+(.+)")

    for line in log.splitlines():
        stripped = line.strip()
        if stripped.startswith("! "):
            errors.append(stripped[2:].strip())
        else:
            m = file_line_re.match(stripped)
            if m:
                msg = m.group(1).strip()
                # Skip meta-lines like "==> Fatal error occurred..."
                if msg and not msg.startswith("==>"):
                    errors.append(msg)
        if "LaTeX Warning" in line:
            warnings.append(stripped)
    return errors, warnings


def _truncate_log(log: str) -> tuple[str, bool]:
    """
    Truncate log to MAX_LOG_SIZE bytes.

    Returns (possibly_truncated_log, was_truncated).
    """
    max_size = settings.MAX_LOG_SIZE
    if len(log.encode("utf-8", errors="replace")) > max_size:
        # Truncate by characters, aiming for byte limit
        truncated = log[:max_size] + "\n... [Log truncated]"
        return truncated, True
    return log, False
