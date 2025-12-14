from pydantic import BaseModel, Field
from typing import Optional, Literal, List
from pathlib import Path

class CompileOptions(BaseModel):
    engine: Literal["pdflatex"] = "pdflatex"
    passes: int = 2
    main_file: Optional[str] = None
    timeout_seconds: int = 20

class CompileResult(BaseModel):
    success: bool
    pdf_path: Optional[Path] = None
    compile_time_ms: int
    log: str
    error_message: Optional[str] = None
    log_truncated: bool = False
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

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
