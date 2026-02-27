# LaTeX API v2 Implementation Plan

## Overview

This document outlines the multi-phase plan to implement the v2 API endpoints for the LaTeX Compiler Service. The v2 API introduces multi-file compilation (without requiring zip), stronger security, guaranteed temp directory cleanup, better error handling, and a comprehensive test suite. The service remains **stateless** -- no database, no file storage, no project IDs. Every request contains the full project snapshot.

### Current State (v1)

The existing codebase is a FastAPI application with:
- `POST /compile/sync` -- accepts a single `.tex` file or `.zip` upload, or raw `code` string
- `POST /compile/validate` -- accepts JSON with `code` string, returns compile diagnostics
- `GET /health` -- basic health check
- No authentication, no rate limiting, no middleware
- Compilation via `pdflatex` subprocess with `-no-shell-escape`
- Known bugs: PDF name detection ignores actual main file name, temp dirs leak, shallow zip detection, inconsistent limits

### Target State (v2)

- `GET /health` -- enhanced with engine list and v2 version
- `POST /v2/compile/sync` -- multi-file upload via multipart/form-data with repeated `files` field
- `POST /v2/compile/zip` -- improved zip upload with mandatory `main_file`
- `POST /v2/compile/validate` -- shared pipeline, JSON response only
- Unified compilation pipeline with guaranteed cleanup
- File path validation, file type whitelist, macro scanning on `.tex`/`.sty`/`.cls`
- Standardized error response schema across all endpoints
- Comprehensive test suite (unit, integration, security, regression)
- v1 endpoints preserved for backward compatibility

### Architecture Decisions

- **Single pipeline module**: All endpoints feed into one `compile_project(work_dir, main_file, options) -> CompileResult` function
- **Two input adapters**: `build_workdir_from_multipart(files, work_dir)` and `build_workdir_from_zip(zip_file, work_dir)`
- **Validation layer**: Shared validators for paths, file types, macros, and limits
- **Try/finally cleanup**: Every request wraps compilation in try/finally to guarantee temp dir deletion

---

## Phase 1: Core Pipeline Refactor

**Goal**: Extract and refactor the compilation logic into a clean, testable pipeline module that both v1 and v2 endpoints can share. Fix the known v1 bugs (PDF name detection, temp dir leaks).

### What to Build

#### 1.1 Create `app/services/pipeline.py` -- Unified Compilation Pipeline

Extract the core compile logic from `app/services/latex_compiler.py` into a new pipeline module:

```
compile_project(work_dir: Path, main_file: str, options: CompileOptions) -> CompileResult
```

This function:
- Verifies `main_file` exists in `work_dir`
- Runs `pdflatex` for N passes with `-no-shell-escape -interaction=nonstopmode -halt-on-error -file-line-error`
- **Fix**: Determines output PDF name from `main_file` stem (e.g., `src/main.tex` -> `src/main.pdf`), not hardcoded `main.pdf`
- Parses log for errors (lines starting with `! `) and warnings (lines containing `LaTeX Warning`)
- Truncates log to 64KB max, sets `log_truncated=true` if exceeded
- Returns `CompileResult` with success, pdf_path, timing, log, errors, warnings

#### 1.2 Create `app/services/validators.py` -- Shared Validation

Centralize all validation logic:

- **`validate_file_path(path: str)`**: Rejects absolute paths, `..` components, backslashes, null bytes, empty strings, paths > 300 chars
- **`validate_file_extension(filename: str)`**: Whitelist only: `.tex`, `.bib`, `.bst`, `.cls`, `.sty`, `.png`, `.jpg`, `.jpeg`, `.pdf`, `.txt`, `.csv`
- **`scan_dangerous_macros(content: bytes, filename: str)`**: Scan `.tex`, `.sty`, `.cls` files for `\write18`, `\immediate\write18`, `\input|`, `\openout`, `\openin`, `\newwrite`, `\newread`. Reject files that fail UTF-8 decode if they are `.tex`/`.sty`/`.cls`.
- **`validate_limits(file_count: int, total_bytes: int, passes: int)`**: Enforce max 500 files, 20MB total, 1-5 passes

#### 1.3 Create `app/services/workdir.py` -- Work Directory Management

- **`create_workdir() -> Path`**: Creates a random temp directory with `latex_job_` prefix
- **`cleanup_workdir(work_dir: Path)`**: Recursively deletes work directory, never raises
- **`safe_write_file(work_dir: Path, relative_path: str, content: bytes)`**: Creates parent dirs, writes file, validates path stays within work_dir (resolves symlinks)

#### 1.4 Update `app/models/compile.py` -- Enhanced Models

- Update `CompileOptions` to include all v2 fields: `engine`, `passes`, `main_file`, `timeout_seconds`, `return_format`
- Add `ErrorResponse` model with fields: `status`, `error_type`, `message`, `errors`, `warnings`, `log`, `log_truncated`
- Add `error_type` enum/literal: `"invalid_input" | "payload_too_large" | "latex_compile_error" | "timeout" | "internal"`

#### 1.5 Patch v1 Endpoints to Use New Pipeline

Update `app/api/routes_compile.py` and `app/services/latex_compiler.py`:
- Wire existing `/compile/sync` and `/compile/validate` to use the new pipeline internally
- Add try/finally cleanup around all temp directory usage to fix the leak bug
- Keep the existing v1 interface signatures unchanged for backward compatibility

### Files Touched
- `app/services/pipeline.py` (new)
- `app/services/validators.py` (new)
- `app/services/workdir.py` (new)
- `app/models/compile.py` (edit)
- `app/services/latex_compiler.py` (edit -- wire to pipeline)
- `app/api/routes_compile.py` (edit -- add cleanup)

### Done When
- `compile_project()` works end-to-end for a single `.tex` file
- PDF name is correctly derived from `main_file` stem
- Temp directories are always cleaned up (verified by test)
- All existing v1 tests still pass
- Dangerous macro scanning works on `.tex`, `.sty`, `.cls`

---

## Phase 2: V2 Multi-File Compile Endpoint

**Goal**: Implement the `POST /v2/compile/sync` endpoint that accepts multiple files via multipart/form-data without requiring a zip archive.

### Context

This is the primary new feature of v2. The Overleaf clone backend will send individual project files (main.tex, chapters, images, styles) as separate upload fields. Each file's `filename` header is the project-relative path (e.g., `src/main.tex`, `figures/diagram.png`).

### What to Build

#### 2.1 Create `app/services/adapters.py` -- Input Adapters

**`build_workdir_from_multipart(files: list[UploadFile], work_dir: Path) -> dict`**:
- Iterate over uploaded files
- For each file: validate path (`validate_file_path`), validate extension (`validate_file_extension`), track cumulative size
- Enforce limits: max 500 files, 20MB total, individual file size within total
- Write each file into work_dir preserving relative paths using `safe_write_file()`
- Scan `.tex`, `.sty`, `.cls` files for dangerous macros
- Return metadata dict: `{file_count, total_bytes}`

**`build_workdir_from_zip(zip_file: Path, work_dir: Path) -> dict`**:
- Open zip and iterate members
- Validate each member path, reject symlinks, reject absolute/traversal paths
- Enforce limits: max file count, max total uncompressed size, max single file size
- Extract files preserving paths using `safe_write_file()`
- Scan text-type files for dangerous macros
- Return metadata dict

#### 2.2 Create `app/api/routes_v2.py` -- V2 Route Handlers

**`POST /v2/compile/sync`** handler:

```python
async def compile_multifile(
    main_file: str = Form(...),
    files: list[UploadFile] = File(...),
    engine: str = Form("pdflatex"),
    passes: int = Form(2),
    return_format: str = Form("pdf", alias="return"),
):
```

Flow:
1. Validate `engine` is supported, `passes` is 1-5
2. Create work dir
3. Call `build_workdir_from_multipart(files, work_dir)` -- validates all files
4. Verify `main_file` exists in work_dir
5. Call `compile_project(work_dir, main_file, options)`
6. If `return_format == "pdf"`: return PDF bytes with `Content-Disposition` and `X-Compile-Time-Ms` headers
7. If `return_format == "json"`: return JSON with logs, errors, warnings, optionally base64 PDF
8. On compile error: return 400 with standardized `ErrorResponse`
9. On validation error: return 422 with `ErrorResponse`
10. On payload too large: return 413
11. Finally: cleanup work dir

#### 2.3 Create `app/api/exception_handlers.py` -- Standardized Error Responses

- Custom exception classes: `CompileError`, `PayloadTooLarge`, `InvalidInput`, `CompileTimeout`
- FastAPI exception handlers that return the standardized JSON error schema
- Never expose Python stack traces

#### 2.4 Register V2 Router in `app/main.py`

- Import and include the v2 router
- Keep v1 routes as-is for backward compatibility

### Files Touched
- `app/services/adapters.py` (new)
- `app/api/routes_v2.py` (new)
- `app/api/exception_handlers.py` (new)
- `app/main.py` (edit -- register v2 router)
- `app/core/config.py` (edit -- add v2-specific settings like MAX_FILE_COUNT, MAX_PATH_LENGTH)

### Done When
- `POST /v2/compile/sync` accepts multiple files and compiles successfully
- Path validation rejects all invalid paths (absolute, traversal, backslash, null bytes, too long)
- File type whitelist enforced
- Size and count limits enforced
- Dangerous macros blocked in `.tex`/`.sty`/`.cls`
- Error responses follow the standardized schema
- Compile time header is returned on success
- Temp dir always cleaned up

---

## Phase 3: V2 Zip Compile and Validate Endpoints

**Goal**: Implement `POST /v2/compile/zip` and `POST /v2/compile/validate` with improved security and shared pipeline.

### Context

The zip endpoint is kept for backward compatibility and convenience for external callers (CLI scripts, simple integrations). The validate endpoint allows checking if code compiles without returning a PDF. Both share the same pipeline and security hardening from Phases 1-2.

### What to Build

#### 3.1 `POST /v2/compile/zip` in `app/api/routes_v2.py`

```python
async def compile_zip(
    file: UploadFile = File(...),
    main_file: str = Form(...),    # Now REQUIRED, no auto-detection
    engine: str = Form("pdflatex"),
    passes: int = Form(2),
    return_format: str = Form("pdf", alias="return"),
):
```

Flow:
1. Validate upload is a zip file (check content type and/or magic bytes)
2. Enforce upload size limit (20MB)
3. Save zip to temp file
4. Create work dir
5. Call `build_workdir_from_zip(zip_path, work_dir)` -- validates all members, enforces limits
6. Verify `main_file` exists in work_dir
7. Call `compile_project(work_dir, main_file, options)`
8. Return result based on `return_format`
9. Finally: cleanup work dir AND temp zip file

Key difference from v1: `main_file` is **required** -- no auto-detection. This eliminates the shallow detection bugs.

#### 3.2 `POST /v2/compile/validate` in `app/api/routes_v2.py`

```python
async def validate_compile(body: ValidateRequest):
```

- Accept JSON body with `code`, `passes`, `engine`
- Create work dir, write code to `main.tex`
- Run through same pipeline
- Return only JSON: `compilable`, `errors`, `warnings`, `log`, `log_truncated`, `compile_time_ms`
- No PDF returned
- Cleanup work dir

#### 3.3 Enhanced Zip Security in `app/services/adapters.py`

The `build_workdir_from_zip()` adapter (created in Phase 2) should handle:
- Reject zip members with absolute paths
- Reject zip members with `..` path components
- Reject symlinks (check `ZipInfo.external_attr` for symlink flag)
- Enforce max file count per zip (500)
- Enforce max total uncompressed size (20MB)
- Enforce max single file uncompressed size
- Apply file type whitelist to all members
- Scan text files for dangerous macros after extraction

### Files Touched
- `app/api/routes_v2.py` (edit -- add zip and validate endpoints)
- `app/services/adapters.py` (edit -- finalize zip adapter)
- `app/models/compile.py` (edit -- ensure ValidateRequest/Response are v2-ready)

### Done When
- `POST /v2/compile/zip` works end-to-end with mandatory `main_file`
- Zip security: all path traversal, symlink, and size attacks are blocked
- `POST /v2/compile/validate` returns correct diagnostics
- All three v2 endpoints share the same pipeline
- Error responses are consistent across all endpoints

---

## Phase 4: Health Endpoint Enhancement and Structured Logging

**Goal**: Upgrade the health endpoint to v2 spec, add structured request logging, and add compile-time observability.

### Context

The v2 health endpoint should report the service version and available engines. Structured logging is needed for observability -- every compile request should log request ID, file count, total bytes, engine, passes, main_file, compile time, and outcome.

### What to Build

#### 4.1 Update `GET /health` in `app/main.py`

Change response from:
```json
{"status": "ok", "version": "0.1.0", "tex_available": true}
```

To v2 spec:
```json
{"status": "ok", "version": "2.0.0", "engines": ["pdflatex"]}
```

Dynamically check which engines are available (`shutil.which()` for `pdflatex`, `xelatex`, `lualatex`).

#### 4.2 Create `app/core/logging.py` -- Structured Logging

- Configure Python `logging` with JSON formatter for production, human-readable for development
- Create a `compile_logger` that logs:
  - `request_id` (generated UUID per request)
  - `file_count`
  - `total_bytes`
  - `engine`
  - `passes`
  - `main_file`
  - `compile_time_ms`
  - `outcome`: `success | compile_error | timeout | invalid_input | internal`

#### 4.3 Add Request ID Middleware

- Generate a UUID for each request
- Attach to response headers (`X-Request-Id`)
- Pass through to compile logger

#### 4.4 Update `app/core/config.py`

- Bump `VERSION` to `"2.0.0"`
- Add v2-specific settings:
  - `MAX_FILE_COUNT: int = 500`
  - `MAX_UPLOAD_SIZE: int = 20 * 1024 * 1024` (bump from 10MB to 20MB)
  - `MAX_PASSES: int = 5`
  - `MAX_LOG_SIZE: int = 64 * 1024`
  - `MAX_PATH_LENGTH: int = 300`

### Files Touched
- `app/main.py` (edit -- health endpoint, middleware)
- `app/core/logging.py` (new)
- `app/core/config.py` (edit -- version bump, new settings)
- `app/services/pipeline.py` (edit -- integrate logging)

### Done When
- `/health` returns v2 format with dynamic engine detection
- Every compile request logs structured data (request ID, timing, outcome)
- Response headers include `X-Request-Id`
- Config version is `2.0.0`
- Existing tests updated for new health response format

---

## Phase 5: Unit and Integration Test Suite

**Goal**: Build a comprehensive test suite covering validation, pipeline logic, API endpoints, and real compilation.

### Context

The current test suite has 13 tests total (6 API + 7 compiler) that all use mocks. v2 needs at minimum 10 integration cases and 10 security cases per the acceptance criteria. Tests should use pytest with FastAPI TestClient and real `pdflatex` where available.

### What to Build

#### 5.1 Test Fixtures in `tests/fixtures/`

Create sample project directories:

- `tests/fixtures/projects/simple/` -- single `main.tex`
- `tests/fixtures/projects/multifile/` -- `main.tex` + `chapters/one.tex`
- `tests/fixtures/projects/nested_main/` -- `src/main.tex`
- `tests/fixtures/projects/with_image/` -- `main.tex` + `figures/a.png`
- `tests/fixtures/projects/with_sty/` -- `main.tex` + `custom.sty`
- `tests/fixtures/projects/with_bib/` -- `main.tex` + `refs.bib`

Zip fixture helpers: functions that create zip files in memory from fixture directories.

#### 5.2 Unit Tests -- `tests/test_validators.py`

Path validation:
- Rejects `../evil.tex`, `/etc/passwd`, `C:\windows\foo`, empty string, null bytes, 301-char path
- Accepts `main.tex`, `src/main.tex`, `figures/a.png`

File type validation:
- Accepts `.tex`, `.bib`, `.bst`, `.cls`, `.sty`, `.png`, `.jpg`, `.jpeg`, `.pdf`, `.txt`, `.csv`
- Rejects `.sh`, `.exe`, `.dll`, `.bin`, `.zip`, `.tar`, `.gz`

Macro scanning:
- Detects `\write18` in `.tex`
- Detects `\immediate\write18` in `.sty`
- Detects `\openout` in `.cls`
- Detects `\input|` pipe
- Allows clean `.tex` with normal LaTeX commands

#### 5.3 Unit Tests -- `tests/test_pipeline.py`

- PDF name derived from `main_file`: `main.tex` -> `main.pdf`, `src/doc.tex` -> `src/doc.pdf`
- Log parsing extracts `! Error` lines
- Log parsing extracts `LaTeX Warning` lines
- Log truncation at 64KB
- Compile timeout returns `error_type: "timeout"`
- Missing `main_file` returns error

#### 5.4 Integration Tests -- `tests/test_v2_api.py`

Use FastAPI TestClient against v2 endpoints:

1. Simple single-file compile via `/v2/compile/sync`
2. Multi-file project: `main.tex` + `chapters/one.tex`
3. Nested main file: `src/main.tex` as `main_file`
4. Project with image: includes `figures/a.png`
5. Project with custom `.sty` package
6. Zip compile via `/v2/compile/zip`
7. Compile error returns 400 with structured error
8. Missing `main_file` returns 422
9. `return=json` returns JSON with logs
10. Health endpoint returns v2 format

These tests should use `@pytest.mark.skipif(shutil.which("pdflatex") is None)` for CI environments without TeX.

#### 5.5 Security Tests -- `tests/test_security.py`

1. Path traversal: `../evil.tex` rejected
2. Absolute path: `/etc/passwd` rejected
3. Backslash path: `src\main.tex` rejected
4. Null byte in path rejected
5. Too many files (501) rejected with 413/422
6. Total size > 20MB rejected with 413
7. Dangerous macro in `.tex` rejected
8. Dangerous macro in `.sty` rejected
9. Zip slip attempt rejected
10. Symlink in zip rejected (if representable)
11. Non-whitelisted file type rejected
12. Path > 300 chars rejected

#### 5.6 Regression Tests -- `tests/test_regression.py`

- PDF name for non-`main.tex` main files (bug #1)
- Temp dir cleanup after success
- Temp dir cleanup after failure
- Temp dir cleanup after timeout
- Nested `.tex` detection in zip

### Files Touched
- `tests/fixtures/` (new directory tree with sample projects)
- `tests/conftest.py` (new -- shared fixtures)
- `tests/test_validators.py` (new)
- `tests/test_pipeline.py` (new)
- `tests/test_v2_api.py` (new)
- `tests/test_security.py` (new)
- `tests/test_regression.py` (new)

### Done When
- All unit tests pass
- Integration tests pass with real `pdflatex` (or are skipped gracefully)
- At least 10 integration test cases
- At least 10 security test cases
- Regression tests cover all documented v1 bugs
- `pytest` runs clean with no failures

---

## Phase 6: Final Integration, Cleanup, and Rollout Prep

**Goal**: Wire everything together, ensure v1 backward compatibility, update configuration for production, and prepare for deployment.

### Context

At this point all v2 endpoints, the pipeline, validators, tests, and logging are built. This phase is about ensuring the whole application works as a cohesive unit, cleaning up any leftover v1 code duplication, and making sure the deployment configuration is ready.

### What to Build

#### 6.1 V1 Backward Compatibility

- Ensure `/compile/sync` and `/compile/validate` still work exactly as before
- These should internally use the new pipeline (done in Phase 1) but their external behavior must not change
- Run the old test suite to confirm

#### 6.2 Configuration Updates

- Update `railpack.json` start command if needed
- Update `pyproject.toml` with any new dependencies
- Ensure `.env` example/template covers new config keys
- Update `requirements.txt` if maintained separately

#### 6.3 Code Cleanup

- Remove any dead code from `app/services/latex_compiler.py` that was replaced by the pipeline
- Ensure consistent import style across all new modules
- Add docstrings to all public functions
- Type annotations on all function signatures

#### 6.4 End-to-End Manual Verification

- Test `/v2/compile/sync` with a real multi-file project
- Test `/v2/compile/zip` with a real zip
- Test `/v2/compile/validate` with valid and invalid code
- Test `/health` returns v2 format
- Test error scenarios: bad paths, too large, dangerous macros
- Verify temp dir cleanup by checking `/tmp` before and after

#### 6.5 Update README.md

- Document all v2 endpoints with curl examples
- Note v1 deprecation timeline
- Update API reference

### Files Touched
- `app/services/latex_compiler.py` (cleanup)
- `app/main.py` (final wiring)
- `pyproject.toml` (edit if needed)
- `requirements.txt` (edit if needed)
- `railpack.json` (edit if needed)
- `README.md` (edit)

### Done When
- All v1 tests pass (backward compatibility)
- All v2 tests pass
- Full pytest suite is green
- Manual end-to-end tests pass
- No temp dir leaks
- README documents all v2 endpoints
- Application starts cleanly with `uvicorn app.main:app`

---

## File Structure After v2

```
latex-api/
├── app/
│   ├── __init__.py
│   ├── main.py                        # FastAPI app, health, v1+v2 routers
│   ├── api/
│   │   ├── routes_compile.py          # v1 endpoints (preserved)
│   │   ├── routes_v2.py               # v2 endpoints (new)
│   │   └── exception_handlers.py      # Standardized error responses (new)
│   ├── core/
│   │   ├── config.py                  # Settings with v2 limits (edited)
│   │   └── logging.py                 # Structured logging (new)
│   ├── models/
│   │   └── compile.py                 # Request/response models (edited)
│   └── services/
│       ├── latex_compiler.py          # v1 compat wrapper (cleaned up)
│       ├── pipeline.py                # Core compile pipeline (new)
│       ├── validators.py              # Path, type, macro validation (new)
│       ├── workdir.py                 # Temp dir management (new)
│       └── adapters.py               # Multipart + zip input adapters (new)
├── tests/
│   ├── conftest.py                    # Shared fixtures (new)
│   ├── fixtures/
│   │   └── projects/                  # Sample LaTeX projects (new)
│   │       ├── simple/
│   │       ├── multifile/
│   │       ├── nested_main/
│   │       ├── with_image/
│   │       ├── with_sty/
│   │       └── with_bib/
│   ├── test_api.py                    # v1 API tests (preserved)
│   ├── test_compiler.py               # v1 compiler tests (preserved)
│   ├── test_validators.py             # Validation unit tests (new)
│   ├── test_pipeline.py               # Pipeline unit tests (new)
│   ├── test_v2_api.py                 # v2 integration tests (new)
│   ├── test_security.py               # Security tests (new)
│   └── test_regression.py             # Regression tests (new)
├── pyproject.toml
├── requirements.txt
├── railpack.json
├── railway.json
├── README.md
├── V2.md
└── PLAN.md
```

---

## Phase Dependencies

```
Phase 1 (Pipeline Refactor)
    |
    v
Phase 2 (Multi-File Endpoint)
    |
    v
Phase 3 (Zip + Validate Endpoints)
    |
    v
Phase 4 (Health + Logging)
    |
    v
Phase 5 (Test Suite)
    |
    v
Phase 6 (Integration + Rollout)
```

Phases are sequential. Each phase builds on the previous. Phase 5 (tests) can partially run in parallel with Phase 4, but the full suite requires all endpoints to be implemented.

---

## Estimated Scope

| Phase | New Files | Edited Files | Test Count (approx) |
|-------|-----------|-------------|---------------------|
| 1     | 3         | 3           | --                  |
| 2     | 3         | 2           | --                  |
| 3     | 0         | 3           | --                  |
| 4     | 1         | 3           | --                  |
| 5     | 7+        | 0           | 40-50               |
| 6     | 0         | 5           | --                  |

---

## Acceptance Criteria (from V2.md)

v2 is done when:

- [ ] Multi-file compile works without zip for nested projects
- [ ] Zip compile still works and is more secure
- [ ] No temp directories leak after requests
- [ ] Dangerous macros are blocked in `.tex`, `.sty`, `.cls`
- [ ] Limits are enforced and tested
- [ ] Test suite covers at least 10 integration cases
- [ ] Test suite covers at least 10 security cases
- [ ] Path validation is thorough
