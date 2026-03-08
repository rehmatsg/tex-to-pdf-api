"""
Shared pytest fixtures for the LaTeX API test suite.
"""

import io
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "projects"

# Whether pdflatex is available (used for skipping real-compile tests)
HAS_PDFLATEX = shutil.which("pdflatex") is not None
HAS_BIBTEX = shutil.which("bibtex") is not None
HAS_BIBER = shutil.which("biber") is not None
HAS_KPSEWHICH = shutil.which("kpsewhich") is not None


def _has_kpsewhich_file(filename: str) -> bool:
    if not HAS_KPSEWHICH:
        return False

    result = subprocess.run(
        ["kpsewhich", filename],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


HAS_BIBLATEX_STY = _has_kpsewhich_file("biblatex.sty")


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def simple_tex() -> bytes:
    """A minimal compilable LaTeX document."""
    return rb"""\documentclass{article}
\begin{document}
Hello, World!
\end{document}
"""


@pytest.fixture
def invalid_tex() -> bytes:
    """A LaTeX document with a compilation error."""
    return rb"""\documentclass{article}
\begin{document}
\unknowncommand
\end{document}
"""


def fixture_project_path(name: str) -> Path:
    """Return the path to a named fixture project directory."""
    p = FIXTURES_DIR / name
    if not p.is_dir():
        raise FileNotFoundError(f"Fixture project not found: {p}")
    return p


def load_fixture_files(name: str) -> dict[str, bytes]:
    """
    Load all files from a fixture project directory.

    Returns a dict mapping relative paths to file contents.
    """
    project_dir = fixture_project_path(name)
    result = {}
    for root, _dirs, files in os.walk(project_dir):
        for fname in files:
            abs_path = Path(root) / fname
            rel_path = abs_path.relative_to(project_dir)
            result[str(rel_path)] = abs_path.read_bytes()
    return result


def make_zip_from_fixture(name: str) -> bytes:
    """Create a zip archive (in memory) from a fixture project directory."""
    files = load_fixture_files(name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path, content in files.items():
            zf.writestr(rel_path, content)
    return buf.getvalue()


def make_zip_from_dict(files: dict[str, bytes]) -> bytes:
    """Create a zip archive (in memory) from a dict of path -> content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path, content in files.items():
            zf.writestr(rel_path, content)
    return buf.getvalue()


# Convenience markers
requires_pdflatex = pytest.mark.skipif(
    not HAS_PDFLATEX, reason="pdflatex not available"
)
requires_bibtex = pytest.mark.skipif(
    not (HAS_PDFLATEX and HAS_BIBTEX),
    reason="pdflatex and bibtex are required",
)
requires_biblatex = pytest.mark.skipif(
    not (HAS_PDFLATEX and HAS_BIBER and HAS_BIBLATEX_STY),
    reason="pdflatex, biber, and biblatex.sty are required",
)
