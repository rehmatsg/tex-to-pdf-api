"""
v1-compatible compilation service.

This module preserves the original compile_latex_sync() interface for backward
compatibility with v1 routes, but internally delegates to the new pipeline.

The work directory is now tracked and returned alongside the result so callers
can guarantee cleanup.
"""

import shutil
import time
import zipfile
from pathlib import Path
from typing import Optional, Tuple, List

from app.core.config import settings
from app.models.compile import CompileOptions, CompileResult
from app.services.pipeline import compile_project, _parse_log_messages
from app.services.validators import scan_dangerous_macros, ValidationError
from app.services.workdir import create_workdir, cleanup_workdir, safe_write_file


def compile_latex_sync(
    source_file_path: Path,
    options: CompileOptions,
) -> CompileResult:
    """
    Compiles a LaTeX project synchronously (v1 interface).

    Args:
        source_file_path: Path to the uploaded .tex or .zip file.
        options: Compilation options.

    Returns:
        CompileResult object. If successful, pdf_path points into a temporary
        work directory that the CALLER must clean up by calling
        cleanup_work_dir(result) after reading the PDF.

    Note:
        The caller is responsible for cleaning up the work directory.
        Use compile_latex_sync_safe() for automatic cleanup with a callback,
        or call cleanup_work_dir() manually after reading the PDF.
    """
    start_time = time.time()
    work_dir = create_workdir()

    try:
        main_file = _setup_workdir_from_source(source_file_path, work_dir, options)
        if main_file is None:
            # _setup_workdir_from_source returns None on error,
            # but we need to return a proper CompileResult.
            # This shouldn't happen -- errors are raised as exceptions.
            return CompileResult(
                success=False,
                compile_time_ms=int((time.time() - start_time) * 1000),
                log="Failed to set up work directory.",
                error_message="Internal error during file setup",
                warnings=[],
                errors=[],
            )

        result = compile_project(work_dir, main_file, options)

        # If compilation failed, clean up immediately since there's no PDF
        # to return.
        if not result.success:
            cleanup_workdir(work_dir)

        # If successful, the caller MUST clean up work_dir after reading
        # the PDF from result.pdf_path.
        return result

    except ValidationError as e:
        cleanup_workdir(work_dir)
        return CompileResult(
            success=False,
            compile_time_ms=int((time.time() - start_time) * 1000),
            log=str(e),
            error_message=str(e),
            warnings=[],
            errors=[str(e)],
        )

    except ValueError as e:
        cleanup_workdir(work_dir)
        return CompileResult(
            success=False,
            compile_time_ms=int((time.time() - start_time) * 1000),
            log=str(e),
            error_message=str(e),
            warnings=[],
            errors=[],
        )

    except Exception as e:
        cleanup_workdir(work_dir)
        return CompileResult(
            success=False,
            compile_time_ms=int((time.time() - start_time) * 1000),
            log=str(e),
            error_message=f"Internal error: {str(e)}",
            warnings=[],
            errors=[],
        )


def cleanup_work_dir(result: CompileResult) -> None:
    """
    Clean up the work directory associated with a CompileResult.

    Safe to call even if pdf_path is None or the directory doesn't exist.
    """
    if result.pdf_path is not None:
        # pdf_path is something like /tmp/latex_job_xxx/main.pdf
        # The work dir is its parent (or grandparent for nested main files).
        # Walk up to find the latex_job_ directory.
        work_dir = result.pdf_path
        while work_dir.name and not work_dir.name.startswith("latex_job_"):
            work_dir = work_dir.parent
        if work_dir.name.startswith("latex_job_"):
            cleanup_workdir(work_dir)


def _setup_workdir_from_source(
    source_file_path: Path,
    work_dir: Path,
    options: CompileOptions,
) -> Optional[str]:
    """
    Set up the work directory from a .tex or .zip source file.

    Returns the main_file relative path to use for compilation.
    Raises ValueError or ValidationError on problems.
    """
    if source_file_path.suffix == ".tex":
        return _setup_from_tex(source_file_path, work_dir)

    elif source_file_path.suffix == ".zip":
        return _setup_from_zip(source_file_path, work_dir, options)

    else:
        raise ValueError("Unsupported file type. Only .tex and .zip supported.")


def _setup_from_tex(source_file_path: Path, work_dir: Path) -> str:
    """Copy a single .tex file into work_dir as main.tex, scanning for macros."""
    content = source_file_path.read_bytes()
    scan_dangerous_macros(content, "main.tex")
    safe_write_file(work_dir, "main.tex", content)
    return "main.tex"


def _setup_from_zip(
    source_file_path: Path,
    work_dir: Path,
    options: CompileOptions,
) -> str:
    """Extract a zip file into work_dir, scanning for dangerous content."""
    with zipfile.ZipFile(source_file_path, "r") as zip_ref:
        # Security: validate all paths before extracting
        for member in zip_ref.namelist():
            if member.startswith("/") or ".." in member:
                raise ValueError(f"Invalid path in zip: {member}")

        zip_ref.extractall(work_dir)

    # Scan all scannable files for dangerous macros
    for tex_file in work_dir.rglob("*.tex"):
        scan_dangerous_macros(tex_file.read_bytes(), tex_file.name)
    for sty_file in work_dir.rglob("*.sty"):
        scan_dangerous_macros(sty_file.read_bytes(), sty_file.name)
    for cls_file in work_dir.rglob("*.cls"):
        scan_dangerous_macros(cls_file.read_bytes(), cls_file.name)

    # Determine main file
    main_file = _determine_main_file(work_dir, options)
    return main_file


def _determine_main_file(work_dir: Path, options: CompileOptions) -> str:
    """
    Determine the main .tex file for compilation.

    Priority:
    1. options.main_file if set and exists
    2. main.tex in root
    3. Single .tex file in root
    4. Error if ambiguous
    """
    if options.main_file:
        if not (work_dir / options.main_file).exists():
            raise ValueError(f"Main file '{options.main_file}' not found in zip.")
        return options.main_file

    if (work_dir / "main.tex").exists():
        return "main.tex"

    tex_files = list(work_dir.glob("*.tex"))
    if len(tex_files) == 1:
        return tex_files[0].name
    elif len(tex_files) > 1:
        raise ValueError("Multiple .tex files found. Please specify 'main_file'.")
    else:
        raise ValueError("No .tex files found in zip.")
