"""
Unit tests for app.services.pipeline.

Covers:
- PDF name derivation from main_file
- Log parsing (errors and warnings)
- Log truncation
- Timeout handling
- Missing main file
"""

import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import pytest

from app.models.compile import CompileOptions, CompileResult
from app.services.pipeline import compile_project, _parse_log_messages, _truncate_log
from app.services.workdir import create_workdir, cleanup_workdir, safe_write_file
from tests.conftest import requires_pdflatex


# =====================================================================
# _parse_log_messages
# =====================================================================


class TestParseLogMessages:
    """Tests for the log parser."""

    def test_extracts_errors(self):
        log = "Some output\n! Undefined control sequence.\nmore output\n! Missing $ inserted."
        errors, warnings = _parse_log_messages(log)
        assert len(errors) == 2
        assert "Undefined control sequence." in errors[0]
        assert "Missing $ inserted." in errors[1]

    def test_extracts_warnings(self):
        log = "LaTeX Warning: Reference `foo' undefined.\nother stuff\nLaTeX Warning: There were undefined references."
        errors, warnings = _parse_log_messages(log)
        assert len(warnings) == 2
        assert "Reference" in warnings[0]
        assert "undefined references" in warnings[1]

    def test_empty_log(self):
        errors, warnings = _parse_log_messages("")
        assert errors == []
        assert warnings == []

    def test_mixed_errors_and_warnings(self):
        log = "! Bad macro.\nLaTeX Warning: Something happened."
        errors, warnings = _parse_log_messages(log)
        assert len(errors) == 1
        assert len(warnings) == 1


# =====================================================================
# _truncate_log
# =====================================================================


class TestTruncateLog:
    """Tests for log truncation."""

    def test_short_log_not_truncated(self):
        log = "Short log output"
        result, truncated = _truncate_log(log)
        assert result == log
        assert truncated is False

    def test_long_log_truncated(self):
        # Create a log exceeding MAX_LOG_SIZE (64KB)
        log = "x" * (70 * 1024)
        result, truncated = _truncate_log(log)
        assert truncated is True
        assert "[Log truncated]" in result


# =====================================================================
# compile_project — mocked subprocess
# =====================================================================


class TestCompileProjectMocked:
    """Tests for compile_project() with mocked subprocess."""

    def _make_workdir_with_tex(
        self, content: bytes, main_file: str = "main.tex"
    ) -> Path:
        work_dir = create_workdir()
        safe_write_file(work_dir, main_file, content)
        return work_dir

    def test_missing_main_file(self):
        work_dir = create_workdir()
        try:
            options = CompileOptions(passes=1, main_file="nonexistent.tex")
            result = compile_project(work_dir, "nonexistent.tex", options)
            assert result.success is False
            assert "not found" in result.error_message
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_successful_compile(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        try:
            # Simulate pdflatex creating a PDF
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="This is pdfTeX...\nOutput written on main.pdf",
            )
            # Create the expected PDF so pipeline detects success
            (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")

            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)
            assert result.success is True
            assert result.pdf_path is not None
            assert result.pdf_path.name == "main.pdf"
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_pdf_name_from_nested_main(self, mock_run):
        """PDF name should be derived from main_file stem, not hardcoded."""
        work_dir = create_workdir()
        try:
            (work_dir / "src").mkdir()
            safe_write_file(work_dir, "src/document.tex", b"\\documentclass{article}")

            mock_run.return_value = MagicMock(returncode=0, stdout="OK")
            # pdflatex outputs PDF in cwd (work_dir), not alongside source
            (work_dir / "document.pdf").write_bytes(b"%PDF-1.4")

            options = CompileOptions(passes=1, main_file="src/document.tex")
            result = compile_project(work_dir, "src/document.tex", options)
            assert result.success is True
            assert result.pdf_path.name == "document.pdf"
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_compile_failure(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"bad latex")
        try:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="! Undefined control sequence.\nl.3 \\badcommand\n",
            )

            options = CompileOptions(passes=2, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)
            assert result.success is False
            assert len(result.errors) > 0
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_timeout(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        try:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="pdflatex", timeout=20)

            options = CompileOptions(passes=1, main_file="main.tex", timeout_seconds=20)
            result = compile_project(work_dir, "main.tex", options)
            assert result.success is False
            assert (
                "timed out" in result.error_message.lower()
                or "timeout" in result.error_message.lower()
            )
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_pdflatex_not_found(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        try:
            mock_run.side_effect = FileNotFoundError("pdflatex not found")

            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)
            assert result.success is False
            assert "not found" in result.error_message.lower()
        finally:
            cleanup_workdir(work_dir)


# =====================================================================
# compile_project — real pdflatex
# =====================================================================


@requires_pdflatex
class TestCompileProjectReal:
    """Integration tests with real pdflatex — skipped if not installed."""

    def test_simple_compile(self):
        work_dir = create_workdir()
        try:
            safe_write_file(
                work_dir,
                "main.tex",
                rb"\documentclass{article}\begin{document}Hello\end{document}",
            )
            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)
            assert result.success is True
            assert result.pdf_path is not None
            assert result.pdf_path.exists()
            # Verify it's actually a PDF
            pdf_bytes = result.pdf_path.read_bytes()
            assert pdf_bytes[:5] == b"%PDF-"
        finally:
            cleanup_workdir(work_dir)

    def test_nested_main_file_real(self):
        work_dir = create_workdir()
        try:
            (work_dir / "src").mkdir()
            safe_write_file(
                work_dir,
                "src/main.tex",
                rb"\documentclass{article}\begin{document}Nested\end{document}",
            )
            options = CompileOptions(passes=1, main_file="src/main.tex")
            result = compile_project(work_dir, "src/main.tex", options)
            assert result.success is True
            assert result.pdf_path.name == "main.pdf"
        finally:
            cleanup_workdir(work_dir)

    def test_compile_error_real(self):
        work_dir = create_workdir()
        try:
            safe_write_file(
                work_dir,
                "main.tex",
                rb"\documentclass{article}\begin{document}\unknowncmd\end{document}",
            )
            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)
            assert result.success is False
            assert len(result.errors) > 0
        finally:
            cleanup_workdir(work_dir)
