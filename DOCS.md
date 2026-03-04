# LaTeX API — Complete Documentation

> **Version 2.0.0** | Stateless LaTeX compilation service built with FastAPI

---

## Table of Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Running the Server](#running-the-server)
  - [Running Tests](#running-tests)
- [API Reference](#api-reference)
  - [Health Check](#health-check)
  - [V2 Endpoints](#v2-endpoints)
    - [POST /v2/compile/sync — Multi-file Compile](#post-v2compilesync--multi-file-compile)
    - [POST /v2/compile/zip — Zip Compile](#post-v2compilezip--zip-compile)
    - [POST /v2/compile/validate — Validate Only](#post-v2compilevalidate--validate-only)
  - [V1 Endpoints (Legacy)](#v1-endpoints-legacy)
    - [POST /compile/sync — Single File Compile](#post-compilesync--single-file-compile)
    - [POST /compile/validate — Validate Code](#post-compilevalidate--validate-code)
- [Response Formats](#response-formats)
  - [Success Responses](#success-responses)
  - [Error Responses](#error-responses)
  - [Error Types Reference](#error-types-reference)
- [Request Headers](#request-headers)
- [Response Headers](#response-headers)
- [Security](#security)
  - [Shell Escape Protection](#shell-escape-protection)
  - [File Path Validation](#file-path-validation)
  - [File Extension Whitelist](#file-extension-whitelist)
  - [Dangerous Macro Scanning](#dangerous-macro-scanning)
  - [Zip Archive Security](#zip-archive-security)
  - [Resource Limits](#resource-limits)
- [Configuration](#configuration)
- [Deployment](#deployment)
  - [Railway](#railway)
  - [Docker (Custom)](#docker-custom)
- [Usage Examples](#usage-examples)
  - [Compile a Single .tex File](#compile-a-single-tex-file)
  - [Compile a Multi-file Project](#compile-a-multi-file-project)
  - [Compile from a Zip Archive](#compile-from-a-zip-archive)
  - [Get JSON Response with Base64 PDF](#get-json-response-with-base64-pdf)
  - [Validate LaTeX Code](#validate-latex-code)
  - [Compile Raw Code (V1)](#compile-raw-code-v1)
  - [Python Client Example](#python-client-example)
  - [JavaScript/Node.js Client Example](#javascriptnodejs-client-example)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)

---

## Overview

LaTeX API is a stateless HTTP service that compiles LaTeX projects into PDFs. It accepts source files via multipart uploads, zip archives, or raw code strings and returns compiled PDFs or structured diagnostics.

**Key properties:**

- **Stateless** — no database, no persistent file storage. Every request is self-contained.
- **Secure** — shell escape disabled, dangerous macros blocked, file paths validated, extensions whitelisted.
- **Observable** — structured JSON logging, unique request IDs on every response.
- **Two API versions** — v2 endpoints under `/v2/` with full multi-file support; v1 endpoints preserved for backward compatibility.

---

## Getting Started

### Prerequisites

- **Python 3.13+**
- **pdflatex** (from TeX Live or MiKTeX)

Install pdflatex:

| Platform | Command |
|----------|---------|
| macOS    | `brew install --cask mactex` (full) or `brew install basictex` (minimal) |
| Ubuntu/Debian | `sudo apt-get install texlive-latex-recommended texlive-latex-extra` |
| Fedora/RHEL | `sudo dnf install texlive-scheme-medium` |
| Windows  | Install [TeX Live](https://tug.org/texlive/) or [MiKTeX](https://miktex.org/) |

### Installation

```bash
# Clone the repo
git clone <repo-url>
cd latex-api

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -e .
```

### Running the Server

```bash
uvicorn app.main:app --reload
```

The server starts at `http://localhost:8000`. Interactive API docs are available at `http://localhost:8000/docs` (Swagger UI) and `http://localhost:8000/redoc` (ReDoc).

### Running Tests

```bash
python -m pytest tests/ -v
```

Tests that require `pdflatex` are automatically skipped if it is not installed. To run only fast unit tests (no pdflatex required):

```bash
python -m pytest tests/ -v -m "not requires_pdflatex"
```

---

## API Reference

### Health Check

Check service status, version, and available LaTeX engines.

```
GET /health
```

**Response** `200 OK`:

```json
{
  "status": "ok",
  "version": "2.0.0",
  "engines": ["pdflatex"]
}
```

The `engines` array is populated dynamically by checking which LaTeX binaries (`pdflatex`, `xelatex`, `lualatex`) are available on the system PATH.

---

### V2 Endpoints

All v2 endpoints are under the `/v2/` prefix. They share a standardized error response format and support structured logging with request IDs.

---

#### POST `/v2/compile/sync` — Multi-file Compile

Upload individual project files as multipart form data. Each file's `filename` header is treated as the project-relative path (e.g., `src/main.tex`, `figures/diagram.png`). This is the primary endpoint for multi-file projects that don't want to bother creating a zip.

**Content-Type:** `multipart/form-data`

**Parameters:**

| Parameter   | Type       | Required | Default    | Description |
|-------------|------------|----------|------------|-------------|
| `main_file` | string     | **Yes**  | —          | Relative path to the main `.tex` file (must match one of the uploaded filenames) |
| `files`     | file[]     | **Yes**  | —          | Project files (repeated form field). Each file's `filename` header is the relative path. |
| `engine`    | string     | No       | `pdflatex` | LaTeX engine to use. Currently only `pdflatex` is supported. |
| `passes`    | integer    | No       | `2`        | Number of compilation passes (1–5). Use 2+ for cross-references/bibliography. |
| `return`    | string     | No       | `pdf`      | Response format: `pdf` (raw binary) or `json` (base64-encoded PDF in JSON). |

**Success Response (return=pdf):** `200 OK`

- Body: raw PDF bytes
- Content-Type: `application/pdf`
- Headers: `Content-Disposition: attachment; filename="output.pdf"`, `X-Compile-Time-Ms`

**Success Response (return=json):** `200 OK`

```json
{
  "status": "ok",
  "pdf_base64": "JVBERi0xLjQg...",
  "compile_time_ms": 1200,
  "errors": [],
  "warnings": ["LaTeX Warning: Label(s) may have changed."],
  "log": "--- Pass 1 ---\nThis is pdfTeX...",
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

**Error Responses:** See [Error Responses](#error-responses).

---

#### POST `/v2/compile/zip` — Zip Compile

Upload a zip archive containing the full project. The `main_file` parameter is **required** — the server will not auto-detect which file to compile.

**Content-Type:** `multipart/form-data`

**Parameters:**

| Parameter   | Type     | Required | Default    | Description |
|-------------|----------|----------|------------|-------------|
| `file`      | file     | **Yes**  | —          | The zip archive containing the project |
| `main_file` | string   | **Yes**  | —          | Path to the main `.tex` file inside the zip |
| `engine`    | string   | No       | `pdflatex` | LaTeX engine. Currently only `pdflatex`. |
| `passes`    | integer  | No       | `2`        | Compilation passes (1–5) |
| `return`    | string   | No       | `pdf`      | Response format: `pdf` or `json` |

The zip is fully validated before extraction: paths are checked for traversal, symlinks are rejected, file extensions are whitelisted, and decompressed sizes are enforced.

**Success/Error Responses:** Same format as `/v2/compile/sync`.

---

#### POST `/v2/compile/validate` — Validate Only

Check whether a LaTeX code string compiles without returning a PDF. Useful for syntax checking, editor integrations, or CI pipelines.

**Content-Type:** `application/json`

**Request Body:**

| Field    | Type    | Required | Default    | Description |
|----------|---------|----------|------------|-------------|
| `code`   | string  | **Yes**  | —          | Raw LaTeX source code |
| `passes` | integer | No       | `1`        | Compilation passes (1–5) |
| `engine` | string  | No       | `pdflatex` | LaTeX engine |

**Response** `200 OK`:

```json
{
  "compilable": true,
  "errors": [],
  "warnings": [],
  "log": "--- Pass 1 ---\nThis is pdfTeX...",
  "log_truncated": false,
  "compile_time_ms": 850
}
```

```json
{
  "compilable": false,
  "errors": ["Undefined control sequence."],
  "warnings": [],
  "log": "--- Pass 1 ---\n...\n! Undefined control sequence.\nl.3 \\badcommand\n...",
  "log_truncated": false,
  "compile_time_ms": 420
}
```

**Error Responses:** Validation errors (empty code, dangerous macros, payload too large) return the standard [Error Response](#error-responses) format.

---

### V1 Endpoints (Legacy)

The original v1 endpoints are preserved for backward compatibility. They do **not** use the `/v2/` prefix.

---

#### POST `/compile/sync` — Single File Compile

Compile a single `.tex` file, a `.zip` project, or raw LaTeX code.

**Content-Type:** `multipart/form-data`

**Parameters:**

| Parameter   | Type     | Required | Default    | Description |
|-------------|----------|----------|------------|-------------|
| `file`      | file     | No*      | —          | A `.tex` file or `.zip` archive |
| `code`      | string   | No*      | —          | Raw LaTeX source code |
| `engine`    | string   | No       | `pdflatex` | LaTeX engine |
| `passes`    | integer  | No       | `2`        | Compilation passes (1–5) |
| `main_file` | string   | No       | —          | Main `.tex` file in a zip. Auto-detected if omitted: looks for `main.tex`, then a single `.tex` file in root. |

> \* Either `file` or `code` must be provided (not both, not neither).

**Success Response:** `200 OK` with raw PDF bytes (`application/pdf`).

**Error Response:** `400` with JSON:

```json
{
  "status": "error",
  "error_type": "latex_compile_error",
  "message": "Compilation failed",
  "errors": ["Undefined control sequence."],
  "warnings": [],
  "log": "...",
  "log_truncated": false
}
```

V1 errors for invalid input use FastAPI's default format:

```json
{
  "detail": "Either 'file' or 'code' must be provided"
}
```

---

#### POST `/compile/validate` — Validate Code

**Content-Type:** `application/json`

**Request Body:**

| Field    | Type    | Required | Default    | Description |
|----------|---------|----------|------------|-------------|
| `code`   | string  | **Yes**  | —          | Raw LaTeX source code |
| `passes` | integer | No       | `1`        | Compilation passes |
| `engine` | string  | No       | `pdflatex` | LaTeX engine |

**Response:** Same as [V2 validate](#post-v2compilevalidate--validate-only).

---

## Response Formats

### Success Responses

Compile endpoints can return two formats depending on the `return` parameter (v2) or always PDF (v1):

**PDF (default):**
- HTTP `200 OK`
- Content-Type: `application/pdf`
- Body: raw PDF bytes
- Header: `Content-Disposition: attachment; filename="output.pdf"`
- Header: `X-Compile-Time-Ms: <milliseconds>`

**JSON (return=json, v2 only):**
- HTTP `200 OK`
- Content-Type: `application/json`

```json
{
  "status": "ok",
  "pdf_base64": "<base64-encoded PDF>",
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
    "files": []
  }
}
```

`textcount.status` values:
- `ok`: summary and file breakdown parsed successfully.
- `partial`: summary parsed, but per-file breakdown failed.
- `unavailable`: `texcount` binary is missing.
- `error`: summary generation or parsing failed.

### Error Responses

All v2 endpoints return errors in a standardized format:

```json
{
  "status": "error",
  "error_type": "invalid_input",
  "message": "Human-readable error description",
  "errors": ["Specific error 1", "Specific error 2"],
  "warnings": ["Warning if any"],
  "log": "Compilation log if available",
  "log_truncated": false
}
```

| Field          | Type     | Description |
|----------------|----------|-------------|
| `status`       | string   | Always `"error"` |
| `error_type`   | string   | Machine-readable error category (see table below) |
| `message`      | string   | Human-readable summary |
| `errors`       | string[] | List of specific error messages extracted from logs |
| `warnings`     | string[] | List of LaTeX warnings extracted from logs |
| `log`          | string   | Raw pdflatex log output (may be empty for pre-compilation errors) |
| `log_truncated`| boolean  | `true` if the log was truncated to fit the 64KB limit |

### Error Types Reference

| `error_type`          | HTTP Status | When it occurs |
|-----------------------|-------------|----------------|
| `invalid_input`       | 422         | Bad file paths, disallowed extensions, unsupported engine, empty code, invalid passes value |
| `payload_too_large`   | 413         | Upload exceeds max size (20MB) or max file count (500) |
| `latex_compile_error` | 400         | pdflatex ran but failed to produce a PDF |
| `timeout`             | 400         | Compilation exceeded the timeout (default 20s) |
| `internal`            | 500         | Unexpected server error |
| `dangerous_macro`     | 422         | Blocked macro detected in `.tex`, `.sty`, or `.cls` file |

---

## Request Headers

| Header          | Description |
|-----------------|-------------|
| `X-Request-Id`  | Optional. If provided, the server echoes it back on the response. If omitted, the server generates a UUID-4. Useful for correlating requests in logs. |
| `Content-Type`  | `multipart/form-data` for compile endpoints, `application/json` for validate endpoints |

---

## Response Headers

All responses include:

| Header              | Description |
|---------------------|-------------|
| `X-Request-Id`      | The request's unique identifier (your provided value or a generated UUID-4) |
| `X-Compile-Time-Ms` | Compilation wall-clock time in milliseconds (only on successful PDF responses) |

---

## Security

### Shell Escape Protection

All pdflatex invocations use the `-no-shell-escape` flag, which prevents LaTeX from executing arbitrary shell commands. This is the single most important security measure.

### File Path Validation

Every uploaded file path (from multipart filenames or zip member names) is validated:

| Rule | Rejected Example |
|------|-----------------|
| No absolute paths | `/etc/passwd` |
| No `..` traversal (per-component check) | `../../etc/passwd`, `foo/../bar` |
| No backslashes | `foo\bar.tex` |
| No null bytes | `foo\x00.tex` |
| Max 300 characters | Very long paths |
| Must have an allowed extension | `script.sh` |

The path validation uses `PurePosixPath.parts` to check each component individually, so filenames like `file..name.tex` (which don't contain a `..` path component) are accepted correctly.

### File Extension Whitelist

Only files with these extensions are accepted:

| Category | Extensions |
|----------|------------|
| LaTeX source | `.tex`, `.bib`, `.bst`, `.cls`, `.sty` |
| Images | `.png`, `.jpg`, `.jpeg`, `.pdf`, `.eps`, `.svg` |
| Data | `.txt`, `.csv` |

Files with any other extension are rejected with a `422` error.

### Dangerous Macro Scanning

Files with extensions `.tex`, `.sty`, and `.cls` are scanned for dangerous macros that could be used for file system access or command execution:

| Macro | Risk |
|-------|------|
| `\write18` | Shell command execution |
| `\immediate\write18` | Immediate shell command execution |
| `\input\|` | Pipe input (command execution) |
| `\openout` | Write to arbitrary files |
| `\openin` | Read arbitrary files |
| `\newwrite` | Register new write stream |
| `\newread` | Register new read stream |

If any of these macros are found, the request is rejected before compilation begins.

### Zip Archive Security

Zip uploads receive additional validation:

- **Symlink rejection** — Zip members with Unix symlink attributes are rejected to prevent symlink-based path escapes.
- **Decompression bomb protection** — Both individual member sizes and cumulative uncompressed sizes are checked against the upload limit before extraction.
- **Per-member validation** — Each member goes through the same path validation, extension whitelist, and macro scanning as multipart uploads.

### Resource Limits

| Limit | Default | Config Variable | Description |
|-------|---------|-----------------|-------------|
| Max upload size | 20 MB | `MAX_UPLOAD_SIZE` | Total size of all uploaded files (or uncompressed zip content) |
| Max file count | 500 | `MAX_FILE_COUNT` | Maximum number of files in a single request |
| Max passes | 5 | `MAX_PASSES` | Maximum pdflatex invocations per request |
| Compile timeout | 20s | `TIMEOUT_SECONDS` | Wall-clock timeout per compilation |
| Max log size | 64 KB | `MAX_LOG_SIZE` | Logs exceeding this are truncated (with `log_truncated: true`) |
| Max path length | 300 | `MAX_PATH_LENGTH` | Maximum characters in a file path |

---

## Configuration

All settings can be overridden via environment variables or a `.env` file in the project root.

| Variable           | Type    | Default      | Description |
|--------------------|---------|--------------|-------------|
| `PROJECT_NAME`     | string  | `LaTeX API`  | Application name (shown in OpenAPI docs) |
| `TIMEOUT_SECONDS`  | integer | `20`         | Compilation timeout in seconds |
| `TEX_BIN_PATH`     | string  | `pdflatex`   | Path to the pdflatex binary (or just the name if it's on PATH) |
| `TEXTCOUNT_BIN_PATH` | string | `texcount` | Path to the texcount binary (or just the name if it's on PATH) |
| `TEXTCOUNT_TIMEOUT_SECONDS` | integer | `5` | Timeout in seconds for texcount subprocess calls |
| `MAX_UPLOAD_SIZE`  | integer | `20971520`   | Maximum upload size in bytes (20 MB) |
| `MAX_FILE_COUNT`   | integer | `500`        | Maximum files per request |
| `MAX_PASSES`       | integer | `5`          | Maximum compilation passes |
| `MAX_LOG_SIZE`     | integer | `65536`      | Maximum log output size in bytes (64 KB) |
| `MAX_PATH_LENGTH`  | integer | `300`        | Maximum file path length in characters |
| `LOG_FORMAT`       | string  | `text`       | Log output format: `text` (human-readable) or `json` (structured, recommended for production) |
| `LOG_LEVEL`        | string  | `INFO`       | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

Example `.env` file:

```env
TIMEOUT_SECONDS=30
MAX_UPLOAD_SIZE=52428800
LOG_FORMAT=json
LOG_LEVEL=INFO
```

---

## Deployment

### Railway

The project includes a `railpack.json` that configures Railway to install Python and TeX Live automatically.

1. Push the repo to GitHub.
2. Create a new Railway project from the repo.
3. Railway detects `railpack.json` and handles the build.
4. The start command is defined in `railpack.json`.

### Docker (Custom)

If deploying elsewhere, your Dockerfile needs Python 3.13+ and TeX Live:

```dockerfile
FROM python:3.13-slim

# Install TeX Live
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      texlive-latex-recommended \
      texlive-latex-extra \
      texlive-fonts-recommended && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Usage Examples

### Compile a Single .tex File

```bash
curl -X POST "http://localhost:8000/v2/compile/sync" \
  -F "main_file=main.tex" \
  -F "files=@main.tex" \
  --output output.pdf
```

### Compile a Multi-file Project

```bash
# Upload multiple files with their relative paths preserved
curl -X POST "http://localhost:8000/v2/compile/sync" \
  -F "main_file=main.tex" \
  -F "files=@main.tex;filename=main.tex" \
  -F "files=@chapters/introduction.tex;filename=chapters/introduction.tex" \
  -F "files=@chapters/conclusion.tex;filename=chapters/conclusion.tex" \
  -F "files=@figures/logo.png;filename=figures/logo.png" \
  -F "files=@bibliography.bib;filename=bibliography.bib" \
  -F "passes=3" \
  --output output.pdf
```

### Compile from a Zip Archive

```bash
curl -X POST "http://localhost:8000/v2/compile/zip" \
  -F "file=@project.zip" \
  -F "main_file=src/main.tex" \
  -F "passes=2" \
  --output output.pdf
```

### Get JSON Response with Base64 PDF

```bash
curl -X POST "http://localhost:8000/v2/compile/sync" \
  -F "main_file=main.tex" \
  -F "files=@main.tex" \
  -F "return=json" | jq .
```

To decode the PDF from the JSON response:

```bash
# Extract and decode the base64 PDF
curl -s -X POST "http://localhost:8000/v2/compile/sync" \
  -F "main_file=main.tex" \
  -F "files=@main.tex" \
  -F "return=json" | jq -r '.pdf_base64' | base64 -d > output.pdf
```

### Validate LaTeX Code

```bash
curl -X POST "http://localhost:8000/v2/compile/validate" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "\\documentclass{article}\n\\begin{document}\nHello, world!\n\\end{document}",
    "passes": 1
  }' | jq .
```

### Compile Raw Code (V1)

```bash
curl -X POST "http://localhost:8000/compile/sync" \
  -F "code=\documentclass{article}\begin{document}Hello\end{document}" \
  --output output.pdf
```

### Python Client Example

```python
import requests
import base64
from pathlib import Path


def compile_latex(
    files: dict[str, bytes],
    main_file: str,
    base_url: str = "http://localhost:8000",
    passes: int = 2,
    return_format: str = "pdf",
) -> bytes | dict:
    """
    Compile a LaTeX project via the v2 multi-file endpoint.

    Args:
        files: Mapping of relative paths to file contents.
        main_file: The main .tex file to compile.
        base_url: API base URL.
        passes: Number of compilation passes.
        return_format: "pdf" for raw bytes, "json" for structured response.

    Returns:
        PDF bytes (if return_format="pdf") or parsed JSON dict.
    """
    multipart_files = [
        ("files", (path, content)) for path, content in files.items()
    ]

    response = requests.post(
        f"{base_url}/v2/compile/sync",
        data={
            "main_file": main_file,
            "passes": passes,
            "return": return_format,
        },
        files=multipart_files,
    )
    response.raise_for_status()

    if return_format == "json":
        data = response.json()
        if data.get("status") == "ok":
            data["pdf_bytes"] = base64.b64decode(data["pdf_base64"])
        return data

    return response.content


# Example usage
if __name__ == "__main__":
    project_files = {
        "main.tex": Path("main.tex").read_bytes(),
        "chapters/intro.tex": Path("chapters/intro.tex").read_bytes(),
    }

    pdf = compile_latex(project_files, main_file="main.tex")
    Path("output.pdf").write_bytes(pdf)
    print(f"Compiled PDF: {len(pdf)} bytes")
```

### JavaScript/Node.js Client Example

```javascript
const fs = require("fs");
const FormData = require("form-data");

async function compileLatex(files, mainFile, options = {}) {
  const {
    baseUrl = "http://localhost:8000",
    passes = 2,
    returnFormat = "pdf",
  } = options;

  const form = new FormData();
  form.append("main_file", mainFile);
  form.append("passes", String(passes));
  form.append("return", returnFormat);

  for (const [path, content] of Object.entries(files)) {
    form.append("files", content, { filename: path });
  }

  const response = await fetch(`${baseUrl}/v2/compile/sync`, {
    method: "POST",
    body: form,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(`Compilation failed: ${error.message}`);
  }

  if (returnFormat === "json") {
    return response.json();
  }

  return Buffer.from(await response.arrayBuffer());
}

// Example usage
async function main() {
  const files = {
    "main.tex": fs.readFileSync("main.tex"),
  };

  const pdf = await compileLatex(files, "main.tex");
  fs.writeFileSync("output.pdf", pdf);
  console.log(`Compiled PDF: ${pdf.length} bytes`);
}

main().catch(console.error);
```

---

## Architecture

```
latex-api/
├── app/
│   ├── main.py                  # FastAPI app, middleware, routers, /health
│   ├── api/
│   │   ├── routes_compile.py    # V1 endpoints (/compile/sync, /compile/validate)
│   │   ├── routes_v2.py         # V2 endpoints (/v2/compile/*)
│   │   └── exception_handlers.py# Standardized error responses
│   ├── core/
│   │   ├── config.py            # Settings (env vars, resource limits)
│   │   └── logging.py           # JSON/text logging, compile event logger
│   ├── models/
│   │   └── compile.py           # Pydantic models (options, result, error, request/response)
│   └── services/
│       ├── pipeline.py          # Core compile_project() — all endpoints funnel through here
│       ├── validators.py        # Path, extension, macro, and limit validation
│       ├── workdir.py           # Temp directory creation, safe file writing, cleanup
│       ├── adapters.py          # Input adapters (multipart files, zip archives)
│       └── latex_compiler.py    # V1-compatible wrapper over pipeline
└── tests/
    ├── conftest.py              # Shared fixtures and helpers
    ├── fixtures/projects/       # Sample LaTeX projects for integration tests
    ├── test_validators.py       # 63 validator unit tests
    ├── test_pipeline.py         # 15 pipeline tests (mocked + real pdflatex)
    ├── test_v2_api.py           # 22 v2 integration tests
    ├── test_security.py         # 22 security tests
    ├── test_regression.py       # 11 regression tests
    ├── test_api.py              # 7 v1 API tests
    └── test_compiler.py         # 8 v1 compiler unit tests
```

**Request flow:**

```
Client request
  → RequestIDMiddleware (assigns/echoes X-Request-Id)
  → Route handler (routes_v2.py or routes_compile.py)
    → Input adapter (adapters.py) — validates + writes files to temp dir
    → compile_project() (pipeline.py) — runs pdflatex, parses logs
    → Response builder — PDF binary or JSON
  → cleanup_workdir() (workdir.py) — guaranteed via try/finally
```

All temp directories are created with `tempfile.mkdtemp(prefix="latex_job_")` and cleaned up in `finally` blocks to prevent disk leaks.

---

## Troubleshooting

**"pdflatex binary not found"**
- Ensure pdflatex is installed and on your PATH: `which pdflatex`
- Or set `TEX_BIN_PATH=/full/path/to/pdflatex` in your environment.

**"File type not allowed"**
- Only whitelisted extensions are accepted. See [File Extension Whitelist](#file-extension-whitelist).
- If you need `.dat`, `.def`, or other extensions, they are not currently supported.

**"Dangerous macro detected"**
- Your `.tex`, `.sty`, or `.cls` file contains a blocked macro. Remove the macro or use a safe alternative.
- `\write18` and `\openout` are the most common triggers.

**Compilation times out**
- Default timeout is 20 seconds. Increase `TIMEOUT_SECONDS` for complex documents.
- Reduce the number of passes if cross-references aren't needed.

**Log is truncated**
- Logs exceeding 64KB are truncated. Check `log_truncated: true` in the response.
- Increase `MAX_LOG_SIZE` if you need full logs for debugging.

**"main_file not found among uploaded files"**
- The `main_file` value must exactly match one of the uploaded filenames (case-sensitive).
- For zip uploads, the path is relative to the zip root (e.g., `src/main.tex`, not `/src/main.tex`).

**Cross-references / bibliography not resolving**
- Set `passes=3` or higher. LaTeX needs multiple passes to resolve `\ref`, `\cite`, table of contents, etc.

**V1 vs V2: which should I use?**
- Use **v2** for new integrations. It has better validation, multi-file support without zip, standardized errors, and structured logging.
- Use **v1** only if you have existing clients that depend on the v1 response format.
