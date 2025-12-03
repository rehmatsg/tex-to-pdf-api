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
- **Deployment**: Ready for Railway (using Nixpacks).

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

## API Usage

### Health Check

```bash
curl http://localhost:8000/health
```

### Compile Single File

```bash
curl -X POST "http://localhost:8000/compile/sync" \
  -F "file=@document.tex" \
  --output output.pdf
```

### Compile Raw Code

```bash
curl -X POST "http://localhost:8000/compile/sync" \
  -F "code=\documentclass{article}\begin{document}Hello\end{document}" \
  --output output.pdf
```

### Compile Zip Project

```bash
curl -X POST "http://localhost:8000/compile/sync" \
  -F "file=@project.zip" \
  -F "main_file=main.tex" \
  --output output.pdf
```

## Deployment on Railway

1. Push this repo to GitHub.
2. Create a new project on Railway from the repo.
3. Railway will automatically detect `nixpacks.toml` and install Python + TeX Live (with `latexmk` and common fonts via APT).
4. The start command is defined in `nixpacks.toml`.

## Configuration

Environment variables (see `app/core/config.py`):

- `TIMEOUT_SECONDS`: Compilation timeout (default 20).
- `MAX_UPLOAD_SIZE`: Max file size in bytes (default 10MB).
