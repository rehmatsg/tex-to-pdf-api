# LaTeX API

A robust and secure LaTeX compilation API built with FastAPI. Supports multi-file projects, zip uploads, and raw code compilation.

## Features

- **Multi-file Compilation** (v2): Upload multiple project files without zipping
- **Zip Compilation**: Upload zip archives with mandatory `main_file`
- **Raw Code Validation**: Check if LaTeX compiles without returning a PDF
- **Bibliography Support**: Automatically runs `bibtex` or `biber` when needed
- **Security**:
  - `-no-shell-escape` enforced
  - Dangerous TeX macros blocked (`\write18`, `\openout`, etc.)
  - File path validation (traversal, absolute paths, backslashes, null bytes)
  - File extension whitelist
  - Upload size limit (20MB), file count limit (500)
- **Observability**: Structured logging, `X-Request-Id` on every response
- **Deployment**: Ready for Railway

## Quick Start

### Local Development

1. Install dependencies:
   ```bash
   pip install -e .
   ```
2. Ensure the LaTeX toolchain is installed (TeX Live).
   - **macOS**: `brew install --cask mactex` (full) or `brew install basictex` (minimal)
   - **Linux**: `sudo apt-get install texlive-latex-recommended texlive-latex-extra texlive-bibtex-extra biber`
   - **Windows**: Install TeX Live or MiKTeX
3. Start the app:
   ```bash
   uvicorn app.main:app --reload
   ```

---

## API Documentation

### Endpoints Overview

| Method | Endpoint               | Description                              |
|--------|------------------------|------------------------------------------|
| GET    | `/health`              | Service health, version, available engines |
| POST   | `/compile/sync`        | v1: Compile single file or code          |
| POST   | `/compile/validate`    | v1: Validate code (JSON)                 |
| POST   | `/v2/compile/sync`     | v2: Multi-file compile (multipart)       |
| POST   | `/v2/compile/zip`      | v2: Zip compile (multipart)              |
| POST   | `/v2/compile/validate` | v2: Validate code (JSON)                 |

---

### Health Check

```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "ok",
  "version": "2.0.0",
  "engines": ["pdflatex"]
}
```

---

### V2 Endpoints

#### POST `/v2/compile/sync` — Multi-file Compile

Upload individual project files as multipart form data. Each file's `filename` header is the project-relative path.

**Parameters (multipart/form-data):**

| Parameter   | Type     | Required | Default    | Description                          |
|-------------|----------|----------|------------|--------------------------------------|
| `main_file` | String   | Yes      | -          | Relative path to the main `.tex` file |
| `files`     | File[]   | Yes      | -          | Project files (repeated field)       |
| `engine`    | String   | No       | `pdflatex` | LaTeX engine                         |
| `passes`    | Int      | No       | `2`        | Compilation passes (1–5). Bibliography jobs auto-run at least 3 LaTeX passes. |
| `return`    | String   | No       | `pdf`      | Response format: `pdf` or `json`     |

**Example:**

```bash
curl -X POST "http://localhost:8000/v2/compile/sync" \
  -F "main_file=main.tex" \
  -F "files=@main.tex" \
  -F "files=@chapters/one.tex" \
  -F "files=@figures/diagram.png" \
  --output output.pdf
```

**Success (return=pdf):** `200 OK` with `application/pdf` body, `X-Compile-Time-Ms` header.

**Success (return=json):**
```json
{
  "status": "ok",
  "pdf_base64": "JVBERi0xLjQg...",
  "compile_time_ms": 1200,
  "errors": [],
  "warnings": [],
  "log": "--- Pass 1 ---\n...",
  "log_truncated": false,
  "textcount": {
    "status": "ok",
    "message": null,
    "totals": {
      "words_total": 12,
      "words_text": 10,
      "words_headers": 2,
      "words_captions": 0,
      "headings": 1,
      "floats": 0,
      "math_inline": 0,
      "math_display": 0
    },
    "files": [
      {
        "path": "main.tex",
        "role": "main",
        "words_total": 12,
        "words_text": 10,
        "words_headers": 2,
        "words_captions": 0,
        "headings": 1,
        "floats": 0,
        "math_inline": 0,
        "math_display": 0
      }
    ]
  }
}
```

---

#### POST `/v2/compile/zip` — Zip Compile

Upload a zip archive containing the project. `main_file` is **required** (no auto-detection).

**Parameters (multipart/form-data):**

| Parameter   | Type   | Required | Default    | Description                          |
|-------------|--------|----------|------------|--------------------------------------|
| `file`      | File   | Yes      | -          | Zip archive                          |
| `main_file` | String | Yes      | -          | Main `.tex` file path inside the zip |
| `engine`    | String | No       | `pdflatex` | LaTeX engine                         |
| `passes`    | Int    | No       | `2`        | Compilation passes (1–5). Bibliography jobs auto-run at least 3 LaTeX passes. |
| `return`    | String | No       | `pdf`      | Response format: `pdf` or `json`     |

**Example:**

```bash
curl -X POST "http://localhost:8000/v2/compile/zip" \
  -F "file=@project.zip" \
  -F "main_file=src/main.tex" \
  --output output.pdf
```

---

#### POST `/v2/compile/validate` — Validate Only

Check if LaTeX code compiles without returning a PDF.

**Request Body (application/json):**

| Field    | Type   | Required | Default    | Description                      |
|----------|--------|----------|------------|----------------------------------|
| `code`   | String | Yes      | -          | Raw LaTeX source code            |
| `passes` | Int    | No       | `1`        | Compilation passes (1–5)         |
| `engine` | String | No       | `pdflatex` | LaTeX engine                     |

**Example:**

```bash
curl -X POST "http://localhost:8000/v2/compile/validate" \
  -H "Content-Type: application/json" \
  -d '{"code": "\\documentclass{article}\\begin{document}Hello\\end{document}"}'
```

**Response:**
```json
{
  "compilable": true,
  "errors": [],
  "warnings": [],
  "log": "--- Pass 1 ---\n...",
  "log_truncated": false,
  "compile_time_ms": 850
}
```

---

### V2 Error Response Format

All v2 endpoints return errors in a standardized format:

```json
{
  "status": "error",
  "error_type": "invalid_input",
  "message": "File type not allowed: '.sh'",
  "errors": [],
  "warnings": [],
  "log": "",
  "log_truncated": false
}
```

| `error_type`          | HTTP Status | Description                    |
|-----------------------|-------------|--------------------------------|
| `invalid_input`       | 422         | Bad paths, extensions, macros  |
| `payload_too_large`   | 413         | Upload exceeds size/count limit|
| `latex_compile_error` | 400         | LaTeX compilation failed       |
| `timeout`             | 400         | Compilation timed out          |
| `internal`            | 500         | Unexpected server error        |

---

### V1 Endpoints (Backward Compatible)

The original v1 endpoints remain available and unchanged.

#### POST `/compile/sync`

| Parameter   | Type   | Required | Default    | Description                                       |
|-------------|--------|----------|------------|---------------------------------------------------|
| `file`      | File   | No*      | -          | A `.tex` file or `.zip` project                   |
| `code`      | String | No*      | -          | Raw LaTeX source code                             |
| `engine`    | String | No       | `pdflatex` | LaTeX engine                                      |
| `passes`    | Int    | No       | `2`        | Compilation passes                                |
| `main_file` | String | No       | -          | Main `.tex` file in a zip (auto-detected if omitted) |

> *Either `file` or `code` must be provided.

#### POST `/compile/validate`

| Field    | Type   | Required | Default    | Description          |
|----------|--------|----------|------------|----------------------|
| `code`   | String | Yes      | -          | Raw LaTeX source     |
| `passes` | Int    | No       | `1`        | Compilation passes   |
| `engine` | String | No       | `pdflatex` | LaTeX engine         |

---

### Response Headers

All responses include:

| Header           | Description                                    |
|------------------|------------------------------------------------|
| `X-Request-Id`   | Unique request identifier (UUID or echoed)     |
| `X-Compile-Time-Ms` | Compilation time in ms (success responses) |

---

## Security

### File Validation

- **Path rules**: No absolute paths, `..` traversal, backslashes, null bytes, or paths > 300 chars
- **Extension whitelist**: `.tex`, `.bib`, `.bst`, `.cls`, `.sty`, `.png`, `.jpg`, `.jpeg`, `.pdf`, `.txt`, `.csv`, `.eps`, `.svg`
- **Macro scanning**: `.tex`, `.sty`, `.cls` files are scanned for `\write18`, `\immediate\write18`, `\input|`, `\openout`, `\openin`, `\newwrite`, `\newread`

### Resource Limits

| Limit              | Value  |
|--------------------|--------|
| Max upload size    | 20 MB  |
| Max file count     | 500    |
| Max passes         | 5      |
| Max log size       | 64 KB  |
| Max path length    | 300    |
| Compile timeout    | 20s    |

---

## Configuration

Environment variables (see `app/core/config.py`):

| Variable          | Default    | Description                      |
|-------------------|------------|----------------------------------|
| `TIMEOUT_SECONDS` | `20`       | Compilation timeout in seconds   |
| `MAX_UPLOAD_SIZE` | `20971520` | Maximum upload size in bytes     |
| `MAX_FILE_COUNT`  | `500`      | Maximum files per request        |
| `MAX_PASSES`      | `5`        | Maximum compilation passes       |
| `MAX_LOG_SIZE`    | `65536`    | Maximum log size in bytes        |
| `MAX_PATH_LENGTH` | `300`      | Maximum file path length         |
| `TEX_BIN_PATH`    | `pdflatex` | Path to the LaTeX binary         |
| `BIBTEX_BIN_PATH` | `bibtex`   | Path to the BibTeX binary        |
| `BIBER_BIN_PATH`  | `biber`    | Path to the biber binary         |
| `TEXTCOUNT_BIN_PATH` | `texcount` | Path to the texcount binary    |
| `TEXTCOUNT_TIMEOUT_SECONDS` | `5` | Timeout for texcount subprocesses (seconds) |
| `LOG_FORMAT`      | `text`     | Log format: `text` or `json`     |
| `LOG_LEVEL`       | `INFO`     | Log level                        |

---

## Deployment on Railway

1. Push this repo to GitHub.
2. Create a new project on Railway from the repo.
3. Railway will automatically detect `railpack.json` and install Python + TeX Live.
4. The start command is defined in `railpack.json`.

---

## Running Tests

```bash
pip install -e .
python -m pytest tests/ -v
```

Tests that require `pdflatex` are automatically skipped if it is not installed.
