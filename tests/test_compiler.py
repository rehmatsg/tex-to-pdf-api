"""
Unit tests for the compilation service (v1 interface).

These tests exercise compile_latex_sync() and the underlying pipeline by
mocking file I/O and subprocess calls.
"""

import pytest
from unittest.mock import patch, MagicMock, mock_open
from pathlib import Path
from app.services.latex_compiler import compile_latex_sync
from app.models.compile import CompileOptions


@pytest.fixture
def mock_subprocess():
    with patch("app.services.pipeline.subprocess.run") as mock:
        yield mock


@pytest.fixture
def mock_workdir(tmp_path):
    """Patch create_workdir to return a real tmp_path and track cleanup."""
    with patch("app.services.latex_compiler.create_workdir") as mock_create:
        mock_create.return_value = tmp_path
        yield tmp_path


def _write_tex_source(
    tmp_path, content=b"\\documentclass{article}\\begin{document}Hello\\end{document}"
):
    """Helper: write a .tex source file for compile_latex_sync to read."""
    src = tmp_path / "source.tex"
    src.write_bytes(content)
    return src


# --- Success / Failure ---


def test_compile_success(mock_workdir, mock_subprocess):
    process_mock = MagicMock()
    process_mock.returncode = 0
    process_mock.stdout = "Output log"
    mock_subprocess.return_value = process_mock

    # Write a real source file
    src = _write_tex_source(mock_workdir.parent)

    # The pipeline will look for the PDF after compilation.
    # Simulate pdflatex creating main.pdf in work_dir.
    (mock_workdir / "main.pdf").write_bytes(b"%PDF-1.4 fake")

    options = CompileOptions(passes=1)
    result = compile_latex_sync(src, options)

    assert result.success is True
    assert "--- Pass 1 ---" in result.log
    assert "Output log" in result.log
    assert result.pdf_path is not None
    assert result.warnings == []
    assert result.errors == []


def test_compile_failure(mock_workdir, mock_subprocess):
    process_mock = MagicMock()
    process_mock.returncode = 1
    process_mock.stdout = "Error log"
    mock_subprocess.return_value = process_mock

    src = _write_tex_source(mock_workdir.parent)

    options = CompileOptions(passes=1)
    result = compile_latex_sync(src, options)

    assert result.success is False
    assert "Error log" in result.log


# --- Log parsing ---


def test_error_parsing(mock_workdir, mock_subprocess):
    process_mock = MagicMock()
    process_mock.returncode = 1
    process_mock.stdout = "This is pdfTeX...\n! Undefined control sequence.\nl.10 \\foo"
    mock_subprocess.return_value = process_mock

    src = _write_tex_source(mock_workdir.parent)

    options = CompileOptions(passes=1)
    result = compile_latex_sync(src, options)

    assert result.success is False
    assert result.error_message == "Undefined control sequence."
    assert "Undefined control sequence." in result.errors


def test_warning_parsing(mock_workdir, mock_subprocess):
    process_mock = MagicMock()
    process_mock.returncode = 0
    process_mock.stdout = "LaTeX Warning: Label(s) may have changed.\n"
    mock_subprocess.return_value = process_mock

    src = _write_tex_source(mock_workdir.parent)
    # Simulate successful PDF creation
    (mock_workdir / "main.pdf").write_bytes(b"%PDF-1.4 fake")

    options = CompileOptions(passes=1)
    result = compile_latex_sync(src, options)

    assert result.success is True
    assert "LaTeX Warning: Label(s) may have changed." in result.warnings


# --- Security ---


def test_dangerous_macro(mock_workdir, mock_subprocess):
    src = _write_tex_source(
        mock_workdir.parent,
        content=rb"\documentclass{article}\begin{document}\write18{rm -rf /}\end{document}",
    )

    options = CompileOptions(passes=1)
    result = compile_latex_sync(src, options)

    assert result.success is False
    assert "Dangerous macro detected" in result.log


# --- Edge cases ---


def test_missing_pdflatex(mock_workdir, mock_subprocess):
    import subprocess as _subprocess

    mock_subprocess.side_effect = FileNotFoundError("pdflatex not found")

    src = _write_tex_source(mock_workdir.parent)

    options = CompileOptions(passes=1)
    result = compile_latex_sync(src, options)

    assert result.success is False
    assert "pdflatex binary not found" in result.error_message


def test_compile_zip_success(mock_workdir, mock_subprocess, tmp_path):
    """Test that a valid zip file is extracted and compiled."""
    import zipfile

    # Create a real zip with main.tex
    zip_path = tmp_path / "project.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "main.tex", r"\documentclass{article}\begin{document}Hello\end{document}"
        )
        zf.writestr("image.png", b"\x89PNG fake image data")

    process_mock = MagicMock()
    process_mock.returncode = 0
    process_mock.stdout = "Output log"
    mock_subprocess.return_value = process_mock

    # Simulate pdflatex creating main.pdf
    (mock_workdir / "main.pdf").write_bytes(b"%PDF-1.4 fake")

    options = CompileOptions(passes=1)
    result = compile_latex_sync(zip_path, options)

    assert result.success is True
    mock_subprocess.assert_called_once()


def test_compile_zip_unsafe_path(mock_workdir, mock_subprocess, tmp_path):
    """Test that zip files with path traversal are rejected."""
    import zipfile

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "../evil.tex", r"\documentclass{article}\begin{document}Evil\end{document}"
        )

    options = CompileOptions(passes=1)
    result = compile_latex_sync(zip_path, options)

    assert result.success is False
    assert "Invalid path in zip" in result.log
