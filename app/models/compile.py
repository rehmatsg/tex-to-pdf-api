from pydantic import BaseModel, Field
from typing import Optional, Literal, List
from pathlib import Path


class CompileOptions(BaseModel):
    """Options controlling a single compilation request."""

    engine: Literal["pdflatex"] = "pdflatex"
    passes: int = 2
    main_file: Optional[str] = None
    timeout_seconds: int = 20


class CompileResult(BaseModel):
    """Result of a compilation attempt."""

    success: bool
    pdf_path: Optional[Path] = None
    work_dir: Optional[Path] = None
    compile_time_ms: int
    log: str
    error_message: Optional[str] = None
    log_truncated: bool = False
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Standardized error response for all v2 endpoints."""

    status: Literal["error"] = "error"
    error_type: str  # "invalid_input" | "payload_too_large" | "latex_compile_error" | "timeout" | "internal"
    message: str
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    log: str = ""
    log_truncated: bool = False


class ValidateRequest(BaseModel):
    code: str
    passes: int = 1
    engine: Literal["pdflatex"] = "pdflatex"


class ValidateResponse(BaseModel):
    compilable: bool
    errors: List[str]
    warnings: List[str]
    log: str
    log_truncated: bool
    compile_time_ms: int
