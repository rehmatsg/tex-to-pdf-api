"""
Standardized exception handling for the v2 API.

Provides custom exception classes and FastAPI exception handlers that return
the unified ErrorResponse schema.  Never exposes Python stack traces.
"""

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.models.compile import ErrorResponse
from app.services.validators import PayloadTooLargeError, ValidationError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all v2 exception handlers on the FastAPI app instance."""

    @app.exception_handler(ValidationError)
    async def validation_error_handler(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        status_code = 422 if exc.error_type == "invalid_input" else 400
        return _error_response(
            status_code=status_code,
            error_type=exc.error_type,
            message=exc.message,
        )

    @app.exception_handler(PayloadTooLargeError)
    async def payload_too_large_handler(
        request: Request, exc: PayloadTooLargeError
    ) -> JSONResponse:
        return _error_response(
            status_code=413,
            error_type="payload_too_large",
            message=exc.message,
        )


def _error_response(
    status_code: int,
    error_type: str,
    message: str,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    log: str = "",
    log_truncated: bool = False,
) -> JSONResponse:
    """Build a JSONResponse from an ErrorResponse model."""
    body = ErrorResponse(
        error_type=error_type,
        message=message,
        errors=errors or [],
        warnings=warnings or [],
        log=log,
        log_truncated=log_truncated,
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
    )
