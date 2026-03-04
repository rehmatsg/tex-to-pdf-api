"""
V2 API route handlers.

Endpoints:
    POST /v2/compile/sync      Multi-file compile (multipart/form-data)
    POST /v2/compile/zip       Zip compile (multipart/form-data)
    POST /v2/compile/validate  Validation-only (JSON body)
"""

import base64
import logging
import os
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.core.config import settings
from app.core.logging import log_compile_event
from app.models.compile import (
    CompileOptions,
    CompileResult,
    ErrorResponse,
    TextCountResponse,
    ValidateRequest,
    ValidateResponse,
)
from app.services.adapters import build_workdir_from_multipart, build_workdir_from_zip
from app.services.pipeline import compile_project
from app.services.textcount import collect_textcount
from app.services.validators import (
    PayloadTooLargeError,
    ValidationError,
    scan_dangerous_macros,
    validate_limits,
)
from app.services.workdir import cleanup_workdir, create_workdir, safe_write_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _compile_error_response(
    status_code: int,
    error_type: str,
    message: str,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    log: str = "",
    log_truncated: bool = False,
) -> JSONResponse:
    """Return a standardized JSON error response."""
    body = ErrorResponse(
        error_type=error_type,
        message=message,
        errors=errors or [],
        warnings=warnings or [],
        log=log,
        log_truncated=log_truncated,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _validation_error(exc: ValidationError) -> JSONResponse:
    """Map a ValidationError to the correct HTTP status + body."""
    if isinstance(exc, PayloadTooLargeError):
        return _compile_error_response(413, exc.error_type, exc.message)
    return _compile_error_response(422, exc.error_type, exc.message)


def _build_compile_response(
    result: CompileResult,
    return_format: str,
    textcount: TextCountResponse | None = None,
) -> Response | JSONResponse:
    """
    Build the HTTP response from a CompileResult.

    On success returns PDF (binary or base64-in-JSON).
    On failure returns a standardized error response.
    """
    if result.success and result.pdf_path and result.pdf_path.exists():
        if return_format == "json":
            pdf_b64 = base64.b64encode(result.pdf_path.read_bytes()).decode("ascii")
            if textcount is None:
                textcount = TextCountResponse(
                    status="error", message="textcount was not collected"
                )
            return JSONResponse(
                content={
                    "status": "ok",
                    "pdf_base64": pdf_b64,
                    "compile_time_ms": result.compile_time_ms,
                    "errors": result.errors,
                    "warnings": result.warnings,
                    "log": result.log,
                    "log_truncated": result.log_truncated,
                    "textcount": textcount.model_dump(),
                }
            )

        # default: return raw PDF
        pdf_bytes = result.pdf_path.read_bytes()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="output.pdf"',
                "X-Compile-Time-Ms": str(result.compile_time_ms),
            },
        )

    # compile failed
    error_type = (
        "timeout"
        if "timed out" in (result.error_message or "")
        else "latex_compile_error"
    )
    return _compile_error_response(
        400,
        error_type,
        result.error_message or "Compilation failed",
        errors=result.errors,
        warnings=result.warnings,
        log=result.log,
        log_truncated=result.log_truncated,
    )


# ---------------------------------------------------------------------------
# POST /v2/compile/sync  —  multi-file compile
# ---------------------------------------------------------------------------


@router.post("/compile/sync")
async def compile_sync_multifile(
    request: Request,
    main_file: str = Form(...),
    files: list[UploadFile] = File(...),
    engine: str = Form("pdflatex"),
    passes: int = Form(2),
    return_format: str = Form("pdf", alias="return"),
):
    """
    Compile a multi-file LaTeX project uploaded as individual files.

    Each uploaded file's ``filename`` header is the project-relative path
    (e.g. ``src/main.tex``, ``figures/diagram.png``).
    """
    request_id = _get_request_id(request)
    t0 = time.monotonic()

    # --- engine guard ---
    if engine != "pdflatex":
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/sync",
            main_file=main_file,
            engine=engine,
            passes=passes,
            outcome="invalid_input",
            error_message=f"Unsupported engine: {engine!r}",
        )
        return _compile_error_response(
            422, "invalid_input", f"Unsupported engine: {engine!r}"
        )

    # --- return format guard ---
    if return_format not in ("pdf", "json"):
        return _compile_error_response(
            422,
            "invalid_input",
            f"Unsupported return format: {return_format!r}. Must be 'pdf' or 'json'.",
        )

    work_dir = create_workdir()

    try:
        # --- populate work dir from uploads ---
        try:
            meta = await build_workdir_from_multipart(files, work_dir, passes)
        except (ValidationError, PayloadTooLargeError) as exc:
            log_compile_event(
                request_id=request_id,
                endpoint="/v2/compile/sync",
                main_file=main_file,
                engine=engine,
                passes=passes,
                file_count=len(files),
                outcome="invalid_input",
                error_message=exc.message,
            )
            return _validation_error(exc)

        # --- verify main_file exists ---
        if not (work_dir / main_file).exists():
            msg = f"main_file '{main_file}' was not found among the uploaded files"
            log_compile_event(
                request_id=request_id,
                endpoint="/v2/compile/sync",
                main_file=main_file,
                engine=engine,
                passes=passes,
                file_count=meta.get("file_count", len(files)),
                total_bytes=meta.get("total_bytes", 0),
                outcome="invalid_input",
                error_message=msg,
            )
            return _compile_error_response(422, "invalid_input", msg)

        # --- compile ---
        options = CompileOptions(
            engine="pdflatex",
            passes=passes,
            main_file=main_file,
            timeout_seconds=settings.TIMEOUT_SECONDS,
        )
        result = compile_project(work_dir, main_file, options)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # --- log compile event ---
        outcome = (
            "success"
            if result.success
            else (
                "timeout"
                if "timed out" in (result.error_message or "")
                else "compile_error"
            )
        )
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/sync",
            main_file=main_file,
            engine=engine,
            passes=passes,
            file_count=meta.get("file_count", len(files)),
            total_bytes=meta.get("total_bytes", 0),
            compile_time_ms=elapsed_ms,
            outcome=outcome,
            error_message=result.error_message if not result.success else None,
        )

        textcount: TextCountResponse | None = None
        if result.success and return_format == "json":
            textcount = collect_textcount(work_dir, main_file)

        # --- build response ---
        return _build_compile_response(result, return_format, textcount=textcount)

    finally:
        cleanup_workdir(work_dir)


# ---------------------------------------------------------------------------
# POST /v2/compile/zip  —  zip compile
# ---------------------------------------------------------------------------


@router.post("/compile/zip")
async def compile_zip(
    request: Request,
    file: UploadFile = File(...),
    main_file: str = Form(...),
    engine: str = Form("pdflatex"),
    passes: int = Form(2),
    return_format: str = Form("pdf", alias="return"),
):
    """
    Compile a LaTeX project from a zip archive.

    The zip is extracted with full security validation (path traversal,
    symlinks, size limits).  ``main_file`` is **required** — no auto-detection.
    """
    request_id = _get_request_id(request)
    t0 = time.monotonic()

    # --- engine guard ---
    if engine != "pdflatex":
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/zip",
            main_file=main_file,
            engine=engine,
            passes=passes,
            outcome="invalid_input",
            error_message=f"Unsupported engine: {engine!r}",
        )
        return _compile_error_response(
            422, "invalid_input", f"Unsupported engine: {engine!r}"
        )

    # --- return format guard ---
    if return_format not in ("pdf", "json"):
        return _compile_error_response(
            422,
            "invalid_input",
            f"Unsupported return format: {return_format!r}. Must be 'pdf' or 'json'.",
        )

    # --- stream upload to a temp file, enforcing size ---
    tmp_zip_fd, tmp_zip_path_str = tempfile.mkstemp(suffix=".zip")
    tmp_zip_path = Path(tmp_zip_path_str)
    total_uploaded = 0

    try:
        with os.fdopen(tmp_zip_fd, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_uploaded += len(chunk)
                if total_uploaded > settings.MAX_UPLOAD_SIZE:
                    log_compile_event(
                        request_id=request_id,
                        endpoint="/v2/compile/zip",
                        main_file=main_file,
                        engine=engine,
                        passes=passes,
                        total_bytes=total_uploaded,
                        outcome="invalid_input",
                        error_message="Uploaded zip exceeds size limit",
                    )
                    return _compile_error_response(
                        413,
                        "payload_too_large",
                        f"Uploaded zip exceeds {settings.MAX_UPLOAD_SIZE} bytes",
                    )
                f.write(chunk)
    except Exception:
        # If streaming failed, clean up temp file
        if tmp_zip_path.exists():
            os.remove(tmp_zip_path)
        raise

    work_dir = create_workdir()

    try:
        # --- extract zip into work dir ---
        try:
            meta = build_workdir_from_zip(tmp_zip_path, work_dir, passes)
        except (ValidationError, PayloadTooLargeError) as exc:
            log_compile_event(
                request_id=request_id,
                endpoint="/v2/compile/zip",
                main_file=main_file,
                engine=engine,
                passes=passes,
                total_bytes=total_uploaded,
                outcome="invalid_input",
                error_message=exc.message,
            )
            return _validation_error(exc)

        # --- verify main_file exists ---
        if not (work_dir / main_file).exists():
            msg = f"main_file '{main_file}' was not found in the zip archive"
            log_compile_event(
                request_id=request_id,
                endpoint="/v2/compile/zip",
                main_file=main_file,
                engine=engine,
                passes=passes,
                file_count=meta.get("file_count", 0),
                total_bytes=meta.get("total_bytes", 0),
                outcome="invalid_input",
                error_message=msg,
            )
            return _compile_error_response(422, "invalid_input", msg)

        # --- compile ---
        options = CompileOptions(
            engine="pdflatex",
            passes=passes,
            main_file=main_file,
            timeout_seconds=settings.TIMEOUT_SECONDS,
        )
        result = compile_project(work_dir, main_file, options)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # --- log compile event ---
        outcome = (
            "success"
            if result.success
            else (
                "timeout"
                if "timed out" in (result.error_message or "")
                else "compile_error"
            )
        )
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/zip",
            main_file=main_file,
            engine=engine,
            passes=passes,
            file_count=meta.get("file_count", 0),
            total_bytes=meta.get("total_bytes", 0),
            compile_time_ms=elapsed_ms,
            outcome=outcome,
            error_message=result.error_message if not result.success else None,
        )

        textcount: TextCountResponse | None = None
        if result.success and return_format == "json":
            textcount = collect_textcount(work_dir, main_file)

        # --- build response ---
        return _build_compile_response(result, return_format, textcount=textcount)

    finally:
        cleanup_workdir(work_dir)
        if tmp_zip_path.exists():
            os.remove(tmp_zip_path)


# ---------------------------------------------------------------------------
# POST /v2/compile/validate  —  validation only
# ---------------------------------------------------------------------------


# No response_model here: the endpoint can return either ValidateResponse
# (success) or a JSONResponse error, so a single response_model would cause
# FastAPI to attempt serialization of error responses through the schema.
@router.post("/compile/validate")
async def validate_compile(payload: ValidateRequest, request: Request):
    """
    Validate whether LaTeX code compiles, without returning a PDF.

    Accepts a JSON body and returns structured diagnostics.
    """
    request_id = _get_request_id(request)
    t0 = time.monotonic()

    code = payload.code or ""
    if not code.strip():
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/validate",
            main_file="main.tex",
            engine=payload.engine,
            passes=payload.passes,
            outcome="invalid_input",
            error_message="'code' must be provided and non-empty",
        )
        return _compile_error_response(
            422, "invalid_input", "'code' must be provided and non-empty"
        )

    code_bytes = code.encode("utf-8")
    if len(code_bytes) > settings.MAX_UPLOAD_SIZE:
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/validate",
            main_file="main.tex",
            engine=payload.engine,
            passes=payload.passes,
            total_bytes=len(code_bytes),
            outcome="invalid_input",
            error_message="Code too large",
        )
        return _compile_error_response(
            413,
            "payload_too_large",
            f"Code too large (max {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB)",
        )

    # --- validate passes ---
    try:
        validate_limits(
            file_count=1, total_bytes=len(code_bytes), passes=payload.passes
        )
    except (ValidationError, PayloadTooLargeError) as exc:
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/validate",
            main_file="main.tex",
            engine=payload.engine,
            passes=payload.passes,
            total_bytes=len(code_bytes),
            outcome="invalid_input",
            error_message=exc.message,
        )
        return _validation_error(exc)

    # --- macro scan ---
    try:
        scan_dangerous_macros(code_bytes, "main.tex")
    except ValidationError as exc:
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/validate",
            main_file="main.tex",
            engine=payload.engine,
            passes=payload.passes,
            total_bytes=len(code_bytes),
            outcome="invalid_input",
            error_message=exc.message,
        )
        return _validation_error(exc)

    work_dir = create_workdir()

    try:
        safe_write_file(work_dir, "main.tex", code_bytes)

        options = CompileOptions(
            engine=payload.engine,
            passes=payload.passes,
            main_file="main.tex",
            timeout_seconds=settings.TIMEOUT_SECONDS,
        )
        result = compile_project(work_dir, "main.tex", options)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # --- log compile event ---
        outcome = (
            "success"
            if result.success
            else (
                "timeout"
                if "timed out" in (result.error_message or "")
                else "compile_error"
            )
        )
        log_compile_event(
            request_id=request_id,
            endpoint="/v2/compile/validate",
            main_file="main.tex",
            engine=payload.engine,
            passes=payload.passes,
            file_count=1,
            total_bytes=len(code_bytes),
            compile_time_ms=elapsed_ms,
            outcome=outcome,
            error_message=result.error_message if not result.success else None,
        )

        return ValidateResponse(
            compilable=result.success,
            errors=result.errors,
            warnings=result.warnings,
            log=result.log,
            log_truncated=result.log_truncated,
            compile_time_ms=result.compile_time_ms,
        )

    finally:
        cleanup_workdir(work_dir)
