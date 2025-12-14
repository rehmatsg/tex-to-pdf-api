from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import Response, JSONResponse
from typing import Optional
from pathlib import Path
import shutil
import tempfile
import os

from app.models.compile import CompileOptions, ValidateRequest, ValidateResponse
from app.services.latex_compiler import compile_latex_sync
from app.core.config import settings

router = APIRouter()

@router.post("/compile/sync")
async def compile_sync(
    file: Optional[UploadFile] = File(None),
    code: Optional[str] = Form(None),
    engine: str = Form("pdflatex"),
    passes: int = Form(2),
    main_file: Optional[str] = Form(None),
):
    # Validate input: either file or code must be provided
    if not file and not code:
        raise HTTPException(status_code=400, detail="Either 'file' or 'code' must be provided")

    # Create options
    options = CompileOptions(
        engine=engine,
        passes=passes,
        main_file=main_file
    )

    # Save input to a temporary location
    # We need to pass a Path to the service. 
    
    # Check file size
    MAX_SIZE = 10 * 1024 * 1024 # 10 MB
    size = 0
    
    # Determine suffix
    suffix = ".tex"
    if file:
        filename = file.filename or "project.tex"
        if filename.endswith(".zip"):
            suffix = ".zip"
        elif not filename.endswith(".tex"):
             raise HTTPException(status_code=400, detail="Only .tex or .zip files are supported")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        if file:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_SIZE:
                    tmp_file.close()
                    os.remove(tmp_file.name)
                    raise HTTPException(status_code=413, detail="File too large (max 10MB)")
                tmp_file.write(chunk)
        else:
            # Write raw code
            code_bytes = code.encode("utf-8")
            if len(code_bytes) > MAX_SIZE:
                 tmp_file.close()
                 os.remove(tmp_file.name)
                 raise HTTPException(status_code=413, detail="Code too large (max 10MB)")
            tmp_file.write(code_bytes)
        
        tmp_path = Path(tmp_file.name)
    
    try:
        result = compile_latex_sync(tmp_path, options)
        
        if result.success and result.pdf_path and result.pdf_path.exists():
            # Read PDF content
            with open(result.pdf_path, "rb") as f:
                pdf_content = f.read()
            
            # Cleanup is tricky here because compile_latex_sync creates a temp dir 
            # and we might want to clean it up.
            # For now, we rely on OS cleanup or future improvements.
            # But we should at least clean up the uploaded temp file.
            
            return Response(
                content=pdf_content,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="output.pdf"',
                    "X-Compile-Time-Ms": str(result.compile_time_ms)
                }
            )
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "error_type": "latex_compile_error",
                    "message": result.error_message or "Compilation failed",
                    "log_truncated": result.log_truncated,
                    "log": result.log
                }
            )
            
    finally:
        # Cleanup uploaded file
        if tmp_path.exists():
            os.remove(tmp_path)

@router.post("/compile/validate", response_model=ValidateResponse)
async def validate_compile(payload: ValidateRequest):
    """
    Validate whether provided LaTeX code compiles without returning the PDF.
    Accepts JSON body with a `code` string and optional `passes`/`engine`.
    """
    code = payload.code or ""
    if not code.strip():
        raise HTTPException(status_code=400, detail="'code' must be provided and non-empty")

    code_bytes = code.encode("utf-8")
    if len(code_bytes) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Code too large (max 10MB)")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".tex") as tmp_file:
        tmp_file.write(code_bytes)
        tmp_path = Path(tmp_file.name)

    try:
        options = CompileOptions(engine=payload.engine, passes=payload.passes, main_file=None)
        result = compile_latex_sync(tmp_path, options)

        return ValidateResponse(
            compilable=result.success,
            errors=result.errors,
            warnings=result.warnings,
            log=result.log,
            log_truncated=result.log_truncated,
            compile_time_ms=result.compile_time_ms,
        )
    finally:
        if tmp_path.exists():
            os.remove(tmp_path)
