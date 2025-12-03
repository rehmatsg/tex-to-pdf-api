# LaTeX API

A robust and secure LaTeX compilation API built with FastAPI.

## Features

- **Sync Compilation**: POST `/compile/sync` accepts:
  - `.tex` files
  - `.zip` projects
  - Raw LaTeX code
- **Security**:
  - `-no-shell-escape` enforced.
  - Dangerous TeX macros (e.g., `\write18`) blocked.
  - File size limits (10MB).
  - Timeouts (20s).
- **Deployment**: Ready for Railway.

## Quick Start

### Local Development

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Ensure `pdflatex` is installed (TeX Live).
   - **macOS**: `brew install --cask mactex` (full) or `brew install basictex` (minimal).
   - **Linux**: `sudo apt-get install texlive-latex-recommended texlive-latex-extra`.
   - **Windows**: Install TeX Live or MiKTeX.
3. Start the app:
   ```bash
   uvicorn app.main:create_app --reload
   ```

---

## API Documentation

### Endpoints

#### Health Check

Check if the API is running.

| Method | Endpoint  | Description          |
|--------|-----------|----------------------|
| GET    | `/health` | Returns health status |

**Response:**
```json
{
  "status": "ok"
}
```

---

#### Compile LaTeX

Compile LaTeX source code into a PDF.

| Method | Endpoint        | Description                     |
|--------|-----------------|--------------------------------|
| POST   | `/compile/sync` | Synchronously compile LaTeX    |

**Request Parameters (multipart/form-data):**

| Parameter   | Type   | Required | Default    | Description                                      |
|-------------|--------|----------|------------|--------------------------------------------------|
| `file`      | File   | No*      | -          | A `.tex` file or `.zip` project                  |
| `code`      | String | No*      | -          | Raw LaTeX source code                            |
| `engine`    | String | No       | `pdflatex` | LaTeX engine to use                              |
| `passes`    | Int    | No       | `2`        | Number of compilation passes                     |
| `main_file` | String | No       | -          | Main `.tex` file in a zip project (auto-detected if not specified) |

> *Either `file` or `code` must be provided.

**Success Response:**

- **Status:** `200 OK`
- **Content-Type:** `application/pdf`
- **Headers:**
  - `Content-Disposition: attachment; filename="output.pdf"`
  - `X-Compile-Time-Ms: <compilation time in milliseconds>`
- **Body:** The compiled PDF file

**Error Response:**

- **Status:** `400 Bad Request`
- **Content-Type:** `application/json`

```json
{
  "status": "error",
  "error_type": "latex_compile_error",
  "message": "Undefined control sequence",
  "log_truncated": false,
  "log": "--- Pass 1 ---\n! Undefined control sequence.\nl.5 \\badcommand\n..."
}
```

**Other Error Codes:**

| Status | Description                                |
|--------|--------------------------------------------|
| `400`  | Missing input or unsupported file type     |
| `413`  | File too large (max 10MB)                  |
| `500`  | Internal server error                      |

---

### Usage Examples

#### Health Check

```bash
curl http://localhost:8000/health
```

#### Compile a `.tex` File

```bash
curl -X POST "http://localhost:8000/compile/sync" \
  -F "file=@document.tex" \
  --output output.pdf
```

#### Compile Raw LaTeX Code

```bash
curl -X POST "http://localhost:8000/compile/sync" \
  -F "code=\documentclass{article}\begin{document}Hello World!\end{document}" \
  --output output.pdf
```

#### Compile a Zip Project

```bash
curl -X POST "http://localhost:8000/compile/sync" \
  -F "file=@project.zip" \
  -F "main_file=main.tex" \
  --output output.pdf
```

#### Compile with Custom Options

```bash
curl -X POST "http://localhost:8000/compile/sync" \
  -F "file=@document.tex" \
  -F "engine=pdflatex" \
  -F "passes=3" \
  --output output.pdf
```

#### JavaScript (fetch)

```javascript
const formData = new FormData();
formData.append('code', '\\documentclass{article}\\begin{document}Hello!\\end{document}');

const response = await fetch('https://your-api.com/compile/sync', {
  method: 'POST',
  body: formData
});

if (response.ok) {
  const blob = await response.blob();
  // Save or display the PDF
} else {
  const error = await response.json();
  console.error(error.message);
}
```

#### Python (requests)

```python
import requests

# Compile raw code
response = requests.post(
    "https://your-api.com/compile/sync",
    data={"code": r"\documentclass{article}\begin{document}Hello!\end{document}"}
)

if response.status_code == 200:
    with open("output.pdf", "wb") as f:
        f.write(response.content)
else:
    print(response.json())
```

```python
# Compile a file
with open("document.tex", "rb") as f:
    response = requests.post(
        "https://your-api.com/compile/sync",
        files={"file": f}
    )
```

---

## Deployment on Railway

1. Push this repo to GitHub.
2. Create a new project on Railway from the repo.
3. Railway will automatically detect `railpack.json` and install Python + TeX Live.
4. The start command is defined in `railpack.json`.

## Configuration

Environment variables (see `app/core/config.py`):

| Variable          | Default | Description                      |
|-------------------|---------|----------------------------------|
| `TIMEOUT_SECONDS` | `20`    | Compilation timeout in seconds   |
| `MAX_UPLOAD_SIZE` | `10MB`  | Maximum file upload size         |
| `TEX_BIN_PATH`    | `pdflatex` | Path to the LaTeX binary      |
