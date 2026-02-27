"""
Regression tests for bugs fixed in v2.

Covers:
- PDF name detection from non-standard main file names (v1 bug #1)
- Temp dir cleanup after success, failure, and timeout
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import pytest

from app.models.compile import CompileOptions
from app.services.pipeline import compile_project
from app.services.workdir import create_workdir, cleanup_workdir, safe_write_file
from tests.conftest import requires_pdflatex, load_fixture_files

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


# =====================================================================
# PDF name derivation (v1 bug #1)
# =====================================================================


class TestPDFNameDerivation:
    """v1 hardcoded 'main.pdf' — v2 derives from main_file stem."""

    @patch("app.services.pipeline.subprocess.run")
    def test_pdf_named_after_document_tex(self, mock_run):
        work_dir = create_workdir()
        try:
            safe_write_file(work_dir, "document.tex", b"\\documentclass{article}")
            mock_run.return_value = MagicMock(returncode=0, stdout="OK")
            (work_dir / "document.pdf").write_bytes(b"%PDF-1.4")

            options = CompileOptions(passes=1, main_file="document.tex")
            result = compile_project(work_dir, "document.tex", options)
            assert result.success is True
            assert result.pdf_path.name == "document.pdf"
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_pdf_named_from_nested_path(self, mock_run):
        work_dir = create_workdir()
        try:
            (work_dir / "src").mkdir()
            safe_write_file(work_dir, "src/thesis.tex", b"\\documentclass{article}")
            mock_run.return_value = MagicMock(returncode=0, stdout="OK")
            # pdflatex outputs PDF in cwd (work_dir), not alongside source
            (work_dir / "thesis.pdf").write_bytes(b"%PDF-1.4")

            options = CompileOptions(passes=1, main_file="src/thesis.tex")
            result = compile_project(work_dir, "src/thesis.tex", options)
            assert result.success is True
            assert result.pdf_path.name == "thesis.pdf"
        finally:
            cleanup_workdir(work_dir)

    @requires_pdflatex
    def test_real_compile_custom_name(self):
        work_dir = create_workdir()
        try:
            safe_write_file(
                work_dir,
                "report.tex",
                rb"\documentclass{article}\begin{document}Test\end{document}",
            )
            options = CompileOptions(passes=1, main_file="report.tex")
            result = compile_project(work_dir, "report.tex", options)
            assert result.success is True
            assert result.pdf_path.name == "report.pdf"
        finally:
            cleanup_workdir(work_dir)


# =====================================================================
# Temp directory cleanup
# =====================================================================


class TestTempDirCleanup:
    """Verify temp directories are always cleaned up after requests."""

    def _count_latex_job_dirs(self) -> list[str]:
        """Find any latex_job_* dirs in the system temp directory."""
        tmp = tempfile.gettempdir()
        return [
            d
            for d in os.listdir(tmp)
            if d.startswith("latex_job_") and os.path.isdir(os.path.join(tmp, d))
        ]

    @requires_pdflatex
    def test_cleanup_after_success(self):
        """Temp dir cleaned up after successful compilation."""
        before = set(self._count_latex_job_dirs())

        files = load_fixture_files("simple")
        upload_files = [("files", (name, content)) for name, content in files.items()]
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=upload_files,
        )
        assert r.status_code == 200

        after = set(self._count_latex_job_dirs())
        # No new temp dirs should remain
        new_dirs = after - before
        assert len(new_dirs) == 0, f"Leaked temp dirs: {new_dirs}"

    @requires_pdflatex
    def test_cleanup_after_compile_error(self):
        """Temp dir cleaned up even when compilation fails."""
        before = set(self._count_latex_job_dirs())

        bad_tex = rb"\documentclass{article}\begin{document}\undefinedcmd\end{document}"
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("main.tex", bad_tex))],
        )
        assert r.status_code == 400

        after = set(self._count_latex_job_dirs())
        new_dirs = after - before
        assert len(new_dirs) == 0, f"Leaked temp dirs: {new_dirs}"

    def test_cleanup_after_validation_error(self):
        """Temp dir cleaned up after validation rejects the request."""
        before = set(self._count_latex_job_dirs())

        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("../evil.tex", b"content"))],
        )
        assert r.status_code == 422

        after = set(self._count_latex_job_dirs())
        new_dirs = after - before
        assert len(new_dirs) == 0, f"Leaked temp dirs: {new_dirs}"


# =====================================================================
# V1 backward compatibility
# =====================================================================


class TestV1BackwardCompat:
    """Ensure v1 endpoints still work after all v2 changes."""

    @requires_pdflatex
    def test_v1_compile_sync_with_file(self):
        tex = rb"\documentclass{article}\begin{document}Hello\end{document}"
        r = client.post(
            "/compile/sync",
            files={"file": ("test.tex", tex, "text/plain")},
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    @requires_pdflatex
    def test_v1_compile_sync_with_code(self):
        r = client.post(
            "/compile/sync",
            data={
                "code": r"\documentclass{article}\begin{document}Hello\end{document}"
            },
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    @requires_pdflatex
    def test_v1_validate(self):
        r = client.post(
            "/compile/validate",
            json={
                "code": r"\documentclass{article}\begin{document}Hello\end{document}"
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["compilable"] is True

    def test_v1_missing_input(self):
        r = client.post("/compile/sync")
        assert r.status_code == 400

    def test_v1_validate_empty_code(self):
        r = client.post("/compile/validate", json={"code": ""})
        assert r.status_code == 400
