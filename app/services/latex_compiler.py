import subprocess
import tempfile
import shutil
import time
import os
from pathlib import Path
from typing import Optional, Tuple, List
from app.models.compile import CompileOptions, CompileResult
from app.core.config import settings

def compile_latex_sync(source_file_path: Path, options: CompileOptions) -> CompileResult:
    """
    Compiles a LaTeX project synchronously.
    
    Args:
        source_file_path: Path to the uploaded .tex or .zip file.
        options: Compilation options.
        
    Returns:
        CompileResult object.
    """
    start_time = time.time()
    
    # Create a temporary working directory
    work_dir = Path(tempfile.mkdtemp(prefix="latex_job_"))
    
    try:
        # Setup files in work_dir
        main_file_name = "main.tex"
        
        # Determine if input is zip or tex
        # For now, assuming single .tex file as per minimal task
        # TODO: Add zip support
        
        if source_file_path.suffix == ".tex":
            # Scan for dangerous macros
            _scan_for_dangerous_macros(source_file_path)
            shutil.copy(source_file_path, work_dir / main_file_name)
        elif source_file_path.suffix == ".zip":
            import zipfile
            with zipfile.ZipFile(source_file_path, 'r') as zip_ref:
                # Security check: Validate paths
                for member in zip_ref.namelist():
                    if member.startswith("/") or ".." in member:
                        raise ValueError(f"Invalid path in zip: {member}")
                
                zip_ref.extractall(work_dir)
            
            # Scan all .tex files in work_dir
            for tex_file in work_dir.glob("**/*.tex"):
                _scan_for_dangerous_macros(tex_file)
            
            # Determine main file
            if options.main_file:
                # Verify it exists
                if not (work_dir / options.main_file).exists():
                     return CompileResult(
                        success=False,
                        compile_time_ms=0,
                        log=f"Main file '{options.main_file}' not found in zip.",
                        error_message="Main file not found",
                        warnings=[],
                        errors=[]
                    )
                main_file_name = options.main_file
            else:
                # Heuristic: Look for main.tex, or the only .tex file
                if (work_dir / "main.tex").exists():
                    main_file_name = "main.tex"
                else:
                    tex_files = list(work_dir.glob("*.tex"))
                    if len(tex_files) == 1:
                        main_file_name = tex_files[0].name
                    elif len(tex_files) > 1:
                         # Ambiguous, fail or pick one? Let's fail for now or pick first?
                         # Better to fail and ask user to specify
                         return CompileResult(
                            success=False,
                            compile_time_ms=0,
                            log="Multiple .tex files found. Please specify 'main_file'.",
                            error_message="Ambiguous main file",
                            warnings=[],
                            errors=[]
                        )
                    else:
                         return CompileResult(
                            success=False,
                            compile_time_ms=0,
                            log="No .tex files found in zip.",
                            error_message="No .tex files found",
                            warnings=[],
                            errors=[]
                        )
        else:
            # Fallback/Error for now until zip is implemented
             return CompileResult(
                success=False,
                compile_time_ms=0,
                log="Unsupported file type. Only .tex and .zip supported.",
                error_message="Unsupported file type",
                warnings=[],
                errors=[]
            )

        log_output = ""
        success = False
        
        # Run compilation passes
        for i in range(options.passes):
            # Construct command
            cmd = [
                settings.TEX_BIN_PATH,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "-no-shell-escape",
                main_file_name
            ]
            
            try:
                result = subprocess.run(
                    cmd,
                    cwd=work_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=options.timeout_seconds,
                    text=True
                )
                
                log_output += f"--- Pass {i+1} ---\n"
                log_output += result.stdout
                
                if result.returncode != 0:
                    break # Stop on error
                
            except subprocess.TimeoutExpired:
                log_output += f"\n--- Timeout after {options.timeout_seconds}s ---"
                return CompileResult(
                    success=False,
                    compile_time_ms=int((time.time() - start_time) * 1000),
                    log=log_output,
                    error_message="Compilation timed out",
                    warnings=[],
                    errors=[]
                )
        
        # Check for output PDF
        # pdflatex usually produces {main_file_name}.pdf -> main.pdf
        expected_pdf = work_dir / "main.pdf"
        
        if expected_pdf.exists():
            success = True
            # We need to keep the PDF file somewhere if we want to return it.
            # But the temp dir will be deleted. 
            # For the service, we might return the path in the temp dir, 
            # and let the caller handle reading/streaming it before cleanup.
            # OR, we can read it into memory here? 
            # Better: Return path, but caller MUST handle cleanup of work_dir?
            # Actually, `compile_latex_sync` creates the temp dir. 
            # If we return a path inside it, we can't delete it here.
            # Let's NOT delete work_dir here if success, and let caller handle it?
            # Or better: Copy PDF to a separate temp file that is managed by caller?
            # For now, let's leave the work_dir cleanup to the caller or use a context manager approach later.
            # BUT, to keep it simple and safe:
            # We will NOT delete work_dir here. We will rely on the caller to clean up.
            # Wait, `tempfile.mkdtemp` creates a dir that persists.
            # We should probably return the work_dir path too so caller can clean it up.
            # Or, we can read the PDF bytes? No, that might be large.
            
            # Let's assume for now we return the path, and we need a mechanism to cleanup.
            # Ideally, we'd use a context manager for the work_dir.
            pass
        else:
            success = False
            
        compile_time = int((time.time() - start_time) * 1000)
        
        # Truncate log if needed (simple implementation)
        if len(log_output) > 50 * 1024:
            log_output = log_output[:50*1024] + "\n... [Truncated]"
            truncated = True
        else:
            truncated = False

        errors, warnings = _parse_log_messages(log_output)

        error_message = None if success else (errors[0] if errors else _parse_latex_error(log_output))

        return CompileResult(
            success=success,
            pdf_path=expected_pdf if success else None,
            compile_time_ms=compile_time,
            log=log_output,
            error_message=error_message,
            log_truncated=truncated,
            warnings=warnings,
            errors=errors
        )

    except Exception as e:
        # Ensure cleanup on crash if possible, or just log
        return CompileResult(
            success=False,
            compile_time_ms=int((time.time() - start_time) * 1000),
            log=str(e),
            error_message=f"Internal error: {str(e)}",
            warnings=[],
            errors=[]
        )

def _parse_latex_error(log: str) -> str:
    """
    Simple parser to extract the first error message from LaTeX log.
    """
    # Look for lines starting with "! "
    for line in log.splitlines():
        if line.startswith("! "):
            return line[2:].strip()
    return "Compilation failed"

def _parse_log_messages(log: str) -> Tuple[List[str], List[str]]:
    """
    Extract errors and warnings from the LaTeX log output.
    Errors are lines starting with '! '.
    Warnings include lines containing 'LaTeX Warning'.
    """
    errors: List[str] = []
    warnings: List[str] = []
    for line in log.splitlines():
        if line.startswith("! "):
            errors.append(line[2:].strip())
        if "LaTeX Warning" in line:
            warnings.append(line.strip())
    return errors, warnings

def _scan_for_dangerous_macros(file_path: Path):
    """
    Scans a TeX file for dangerous macros.
    Raises ValueError if found.
    """
    dangerous = [
        r"\write18", r"\immediate\write18", 
        r"\input|", r"\openout", r"\openin",
        r"\newwrite", r"\newread"
    ]
    
    try:
        content = file_path.read_text(errors="ignore")
        for macro in dangerous:
            if macro in content:
                raise ValueError(f"Dangerous macro detected: {macro}")
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        # Ignore read errors or treat as suspicious?
        # For now, ignore non-text files or encoding issues
        pass
