"""
Core compilation pipeline for the LaTeX compiler service.

This module provides the single `compile_project()` function that all endpoints
(v1 and v2) funnel through. It handles:
- Main file verification
- Multi-pass pdflatex invocation with -no-shell-escape
- Automatic bibliography orchestration via bibtex / biber
- Output PDF detection based on actual main_file stem
- Log parsing for errors and warnings
- Log truncation
- Compile timeout handling
"""

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from app.core.config import settings
from app.models.compile import CompileOptions, CompileResult

# Pre-compiled regex for -file-line-error format: ./file.tex:123: Error message
_FILE_LINE_RE = re.compile(r"^\./[^:]+:\d+:\s+(.+)")
_BIBER_ERROR_RE = re.compile(r"^(?:ERROR|FATAL)\s*-\s*(.+)$")
_BIBER_WARNING_RE = re.compile(r"^(?:WARN(?:ING)?)\s*-\s*(.+)$")

BackendName = Literal["bibtex", "biber"]


@dataclass
class _StepExecution:
    """Captured output and status from one subprocess invocation."""

    label: str
    output: str
    returncode: Optional[int] = None
    timed_out: bool = False
    missing_binary_message: Optional[str] = None


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

    main_stem = Path(main_file).stem
    compile_cwd = work_dir
    log_sections: list[str] = []
    backend_warnings: list[str] = []
    final_tex_warnings: list[str] = []

    # First pdflatex pass determines whether bibliography tooling is needed.
    first_pass = _run_pdflatex_step(
        main_file=main_file,
        compile_cwd=compile_cwd,
        timeout_seconds=options.timeout_seconds,
        pass_number=1,
    )
    log_sections.append(_format_log_section(first_pass))

    if first_pass.missing_binary_message:
        return _missing_binary_result(
            start_time=start_time,
            log_sections=log_sections,
            message=first_pass.missing_binary_message,
        )

    first_errors, first_warnings = _parse_latex_log_messages(first_pass.output)
    if first_pass.timed_out:
        return _timeout_result(
            start_time=start_time,
            log_sections=log_sections,
            timeout_seconds=options.timeout_seconds,
            label=first_pass.label,
            errors=first_errors,
            warnings=first_warnings,
        )

    if first_pass.returncode != 0:
        return _failure_result(
            start_time=start_time,
            log_sections=log_sections,
            errors=first_errors,
            warnings=first_warnings,
            error_message=first_errors[0] if first_errors else "Compilation failed",
        )

    bibliography_backend = _detect_bibliography_backend(work_dir, main_stem)
    total_tex_passes = (
        options.passes if bibliography_backend is None else max(options.passes, 3)
    )

    if bibliography_backend is None:
        final_tex_warnings = first_warnings
    else:
        backend_step = _run_backend_step(
            backend=bibliography_backend,
            compile_cwd=compile_cwd,
            main_stem=main_stem,
            timeout_seconds=options.timeout_seconds,
        )
        log_sections.append(_format_log_section(backend_step))

        backend_errors, backend_step_warnings = _parse_backend_messages(
            bibliography_backend, backend_step.output
        )
        backend_warnings = backend_step_warnings

        if backend_step.missing_binary_message:
            return _missing_binary_result(
                start_time=start_time,
                log_sections=log_sections,
                message=backend_step.missing_binary_message,
                warnings=backend_warnings,
            )

        if backend_step.timed_out:
            return _timeout_result(
                start_time=start_time,
                log_sections=log_sections,
                timeout_seconds=options.timeout_seconds,
                label=backend_step.label,
                errors=backend_errors,
                warnings=backend_warnings,
            )

        if backend_step.returncode != 0:
            fallback_message = (
                "Biber failed" if bibliography_backend == "biber" else "BibTeX failed"
            )
            if not backend_errors:
                backend_errors = [fallback_message]
            return _failure_result(
                start_time=start_time,
                log_sections=log_sections,
                errors=backend_errors,
                warnings=backend_warnings,
                error_message=backend_errors[0] if backend_errors else fallback_message,
            )

    for pass_number in range(2, total_tex_passes + 1):
        tex_step = _run_pdflatex_step(
            main_file=main_file,
            compile_cwd=compile_cwd,
            timeout_seconds=options.timeout_seconds,
            pass_number=pass_number,
        )
        log_sections.append(_format_log_section(tex_step))

        if tex_step.missing_binary_message:
            return _missing_binary_result(
                start_time=start_time,
                log_sections=log_sections,
                message=tex_step.missing_binary_message,
                warnings=backend_warnings,
            )

        tex_errors, tex_warnings = _parse_latex_log_messages(tex_step.output)
        if tex_step.timed_out:
            return _timeout_result(
                start_time=start_time,
                log_sections=log_sections,
                timeout_seconds=options.timeout_seconds,
                label=tex_step.label,
                errors=tex_errors,
                warnings=backend_warnings + tex_warnings,
            )

        if tex_step.returncode != 0:
            return _failure_result(
                start_time=start_time,
                log_sections=log_sections,
                errors=tex_errors,
                warnings=backend_warnings + tex_warnings,
                error_message=tex_errors[0] if tex_errors else "Compilation failed",
            )

        final_tex_warnings = tex_warnings

    expected_pdf = _find_expected_pdf(work_dir, main_file)
    warnings = backend_warnings + final_tex_warnings

    if not expected_pdf.exists():
        return _failure_result(
            start_time=start_time,
            log_sections=log_sections,
            errors=[],
            warnings=warnings,
            error_message="Compilation failed",
        )

    return CompileResult(
        success=True,
        pdf_path=expected_pdf,
        compile_time_ms=int((time.time() - start_time) * 1000),
        log=_join_log_sections(log_sections),
        error_message=None,
        log_truncated=_is_truncated(log_sections),
        warnings=warnings,
        errors=[],
    )


def _run_pdflatex_step(
    main_file: str,
    compile_cwd: Path,
    timeout_seconds: int,
    pass_number: int,
) -> _StepExecution:
    return _run_step(
        label=f"Pass {pass_number}",
        cmd=[
            settings.TEX_BIN_PATH,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "-no-shell-escape",
            main_file,
        ],
        cwd=compile_cwd,
        timeout_seconds=timeout_seconds,
        missing_binary_message="pdflatex binary not found",
    )


def _run_backend_step(
    backend: BackendName,
    compile_cwd: Path,
    main_stem: str,
    timeout_seconds: int,
) -> _StepExecution:
    if backend == "biber":
        return _run_step(
            label="Bibliography (biber)",
            cmd=[settings.BIBER_BIN_PATH, main_stem],
            cwd=compile_cwd,
            timeout_seconds=timeout_seconds,
            missing_binary_message="biber binary not found",
        )

    return _run_step(
        label="Bibliography (bibtex)",
        cmd=[settings.BIBTEX_BIN_PATH, main_stem],
        cwd=compile_cwd,
        timeout_seconds=timeout_seconds,
        missing_binary_message="bibtex binary not found",
    )


def _run_step(
    label: str,
    cmd: list[str],
    cwd: Path,
    timeout_seconds: int,
    missing_binary_message: str,
) -> _StepExecution:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            text=True,
        )
        return _StepExecution(
            label=label,
            output=result.stdout or "",
            returncode=result.returncode,
        )
    except FileNotFoundError:
        return _StepExecution(
            label=label,
            output="",
            missing_binary_message=missing_binary_message,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return _StepExecution(label=label, output=output, timed_out=True)


def _detect_bibliography_backend(
    work_dir: Path,
    main_stem: str,
) -> Optional[BackendName]:
    bcf_path = work_dir / f"{main_stem}.bcf"
    if bcf_path.exists():
        return "biber"

    aux_path = work_dir / f"{main_stem}.aux"
    if aux_path.exists():
        aux_text = aux_path.read_text(encoding="utf-8", errors="ignore")
        if r"\bibdata" in aux_text:
            return "bibtex"

    return None


def _find_expected_pdf(work_dir: Path, main_file: str) -> Path:
    main_path = Path(main_file)
    expected_pdf = work_dir / f"{main_path.stem}.pdf"
    if expected_pdf.exists():
        return expected_pdf
    return work_dir / main_path.parent / f"{main_path.stem}.pdf"


def _format_log_section(step: _StepExecution) -> str:
    output = step.output or ""
    return f"--- {step.label} ---\n{output}"


def _join_log_sections(log_sections: list[str]) -> str:
    log_output = "".join(log_sections)
    truncated_log, _ = _truncate_log(log_output)
    return truncated_log


def _is_truncated(log_sections: list[str]) -> bool:
    _, truncated = _truncate_log("".join(log_sections))
    return truncated


def _missing_binary_result(
    start_time: float,
    log_sections: list[str],
    message: str,
    warnings: Optional[list[str]] = None,
) -> CompileResult:
    return _failure_result(
        start_time=start_time,
        log_sections=log_sections,
        errors=[message],
        warnings=warnings or [],
        error_message=message,
    )


def _timeout_result(
    start_time: float,
    log_sections: list[str],
    timeout_seconds: int,
    label: str,
    errors: list[str],
    warnings: list[str],
) -> CompileResult:
    log_sections = log_sections + [
        f"\n--- Timeout after {timeout_seconds}s during {label} ---"
    ]
    log_output = "".join(log_sections)
    log_output, truncated = _truncate_log(log_output)
    return CompileResult(
        success=False,
        compile_time_ms=int((time.time() - start_time) * 1000),
        log=log_output,
        error_message="Compilation timed out",
        log_truncated=truncated,
        warnings=warnings,
        errors=errors,
    )


def _failure_result(
    start_time: float,
    log_sections: list[str],
    errors: list[str],
    warnings: list[str],
    error_message: str,
) -> CompileResult:
    log_output = "".join(log_sections)
    log_output, truncated = _truncate_log(log_output)
    return CompileResult(
        success=False,
        compile_time_ms=int((time.time() - start_time) * 1000),
        log=log_output,
        error_message=error_message,
        log_truncated=truncated,
        warnings=warnings,
        errors=errors,
    )


def _parse_log_messages(log: str) -> tuple[list[str], list[str]]:
    """
    Backward-compatible log parser used by tests and callers.

    Delegates to the LaTeX parser for historic behavior.
    """
    return _parse_latex_log_messages(log)


def _parse_latex_log_messages(log: str) -> tuple[list[str], list[str]]:
    """
    Extract errors and warnings from LaTeX log output.

    Errors are detected in two formats:
    - Classic: lines starting with ``! `` (e.g. ``! Undefined control sequence.``)
    - File-line-error: ``./file.tex:42: Error message`` (from ``-file-line-error`` flag)

    Warnings: lines containing ``LaTeX Warning``.

    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    for line in log.splitlines():
        stripped = line.strip()
        if stripped.startswith("! "):
            errors.append(stripped[2:].strip())
        else:
            match = _FILE_LINE_RE.match(stripped)
            if match:
                msg = match.group(1).strip()
                if msg and not msg.startswith("==>"):
                    errors.append(msg)
        if "LaTeX Warning" in line:
            warnings.append(stripped)
    return errors, warnings


def _parse_backend_messages(
    backend: BackendName,
    log: str,
) -> tuple[list[str], list[str]]:
    if backend == "biber":
        return _parse_biber_messages(log)
    return _parse_bibtex_messages(log)


def _parse_biber_messages(log: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for line in log.splitlines():
        stripped = line.strip()
        error_match = _BIBER_ERROR_RE.match(stripped)
        warning_match = _BIBER_WARNING_RE.match(stripped)

        if error_match:
            errors.append(error_match.group(1).strip())
        elif warning_match:
            warnings.append(warning_match.group(1).strip())

    return errors, warnings


def _parse_bibtex_messages(log: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for line in log.splitlines():
        stripped = line.strip()
        if stripped.startswith("Warning--"):
            warnings.append(stripped)
        elif stripped.startswith("I couldn't"):
            errors.append(stripped)

    return errors, warnings


def _truncate_log(log: str) -> tuple[str, bool]:
    """
    Truncate log to MAX_LOG_SIZE bytes.

    Returns (possibly_truncated_log, was_truncated).
    """
    max_size = settings.MAX_LOG_SIZE
    if len(log.encode("utf-8", errors="replace")) > max_size:
        truncated = log[:max_size] + "\n... [Log truncated]"
        return truncated, True
    return log, False
