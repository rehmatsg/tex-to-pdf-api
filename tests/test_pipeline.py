"""
Unit and integration tests for app.services.pipeline.

Covers:
- PDF name derivation from main_file
- Bibliography backend orchestration
- Log parsing and truncation
- Timeout and missing-binary handling
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

import pytest

from app.core.config import settings
from app.models.compile import CompileOptions
from app.services.pipeline import (
    _parse_log_messages,
    _truncate_log,
    compile_project,
)
from app.services.workdir import cleanup_workdir, create_workdir, safe_write_file
from tests.conftest import (
    load_fixture_files,
    requires_biblatex,
    requires_bibtex,
    requires_pdflatex,
)


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

    def _tex_success(self, stdout: str = "This is pdfTeX...\n") -> MagicMock:
        return MagicMock(returncode=0, stdout=stdout)

    def _backend_success(self, stdout: str = "") -> MagicMock:
        return MagicMock(returncode=0, stdout=stdout)

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
            mock_run.return_value = self._tex_success(
                "This is pdfTeX...\nOutput written on main.pdf"
            )
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
        work_dir = create_workdir()
        try:
            (work_dir / "src").mkdir()
            safe_write_file(work_dir, "src/document.tex", b"\\documentclass{article}")

            mock_run.return_value = self._tex_success("OK")
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
            assert "pdflatex binary not found" == result.error_message
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_biblatex_uses_biber_and_promotes_passes(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        calls: list[list[str]] = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == settings.TEX_BIN_PATH:
                pass_number = sum(1 for call in calls if call[0] == settings.TEX_BIN_PATH)
                if pass_number == 1:
                    (work_dir / "main.bcf").write_text("bcf", encoding="utf-8")
                    (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                    return self._tex_success(
                        "LaTeX Warning: Citation `knuth1984' undefined.\n"
                    )
                return self._tex_success()

            assert cmd == [settings.BIBER_BIN_PATH, "main"]
            return self._backend_success("WARN - backend note\n")

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is True
            assert [cmd[0] for cmd in calls] == [
                settings.TEX_BIN_PATH,
                settings.BIBER_BIN_PATH,
                settings.TEX_BIN_PATH,
                settings.TEX_BIN_PATH,
            ]
            assert result.warnings == ["backend note"]
            assert "--- Bibliography (biber) ---" in result.log
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_bibtex_uses_aux_and_promotes_passes(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        calls: list[list[str]] = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == settings.TEX_BIN_PATH:
                pass_number = sum(1 for call in calls if call[0] == settings.TEX_BIN_PATH)
                if pass_number == 1:
                    (work_dir / "main.aux").write_text(
                        r"\relax" + "\n" + r"\bibdata{refs}",
                        encoding="utf-8",
                    )
                    (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                return self._tex_success()

            assert cmd == [settings.BIBTEX_BIN_PATH, "main"]
            return self._backend_success("Warning--backend note\n")

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=2, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is True
            assert [cmd[0] for cmd in calls] == [
                settings.TEX_BIN_PATH,
                settings.BIBTEX_BIN_PATH,
                settings.TEX_BIN_PATH,
                settings.TEX_BIN_PATH,
            ]
            assert result.warnings == ["Warning--backend note"]
            assert "--- Bibliography (bibtex) ---" in result.log
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_biber_wins_when_bcf_and_aux_exist(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        calls: list[list[str]] = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == settings.TEX_BIN_PATH:
                pass_number = sum(1 for call in calls if call[0] == settings.TEX_BIN_PATH)
                if pass_number == 1:
                    (work_dir / "main.bcf").write_text("bcf", encoding="utf-8")
                    (work_dir / "main.aux").write_text(
                        r"\relax" + "\n" + r"\bibdata{refs}",
                        encoding="utf-8",
                    )
                    (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                return self._tex_success()
            return self._backend_success()

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=2, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is True
            assert calls[1] == [settings.BIBER_BIN_PATH, "main"]
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_bibliography_nested_main_uses_main_stem(self, mock_run):
        work_dir = create_workdir()
        calls: list[list[str]] = []
        try:
            (work_dir / "src").mkdir()
            safe_write_file(work_dir, "src/main.tex", b"\\documentclass{article}")

            def side_effect(cmd, **kwargs):
                calls.append(cmd)
                if cmd[0] == settings.TEX_BIN_PATH:
                    pass_number = sum(
                        1 for call in calls if call[0] == settings.TEX_BIN_PATH
                    )
                    if pass_number == 1:
                        (work_dir / "main.bcf").write_text("bcf", encoding="utf-8")
                        (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                    return self._tex_success()
                return self._backend_success()

            mock_run.side_effect = side_effect

            options = CompileOptions(passes=1, main_file="src/main.tex")
            result = compile_project(work_dir, "src/main.tex", options)

            assert result.success is True
            assert calls[1] == [settings.BIBER_BIN_PATH, "main"]
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_plain_document_keeps_requested_pass_count(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        calls: list[list[str]] = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
            return self._tex_success()

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=2, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is True
            assert calls == [
                [
                    settings.TEX_BIN_PATH,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-file-line-error",
                    "-no-shell-escape",
                    "main.tex",
                ],
                [
                    settings.TEX_BIN_PATH,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-file-line-error",
                    "-no-shell-escape",
                    "main.tex",
                ],
            ]
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_bibliography_passes_five_runs_five_tex_passes(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        calls: list[list[str]] = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == settings.TEX_BIN_PATH:
                pass_number = sum(1 for call in calls if call[0] == settings.TEX_BIN_PATH)
                if pass_number == 1:
                    (work_dir / "main.bcf").write_text("bcf", encoding="utf-8")
                (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                return self._tex_success()
            return self._backend_success()

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=5, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is True
            tex_calls = [cmd for cmd in calls if cmd[0] == settings.TEX_BIN_PATH]
            backend_calls = [cmd for cmd in calls if cmd[0] == settings.BIBER_BIN_PATH]
            assert len(tex_calls) == 5
            assert len(backend_calls) == 1
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_missing_biber_fails_even_if_initial_pdf_exists(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")

        def side_effect(cmd, **kwargs):
            if cmd[0] == settings.TEX_BIN_PATH:
                (work_dir / "main.bcf").write_text("bcf", encoding="utf-8")
                (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                return self._tex_success()
            raise FileNotFoundError("biber not found")

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is False
            assert result.pdf_path is None
            assert result.error_message == "biber binary not found"
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_missing_bibtex_fails_even_if_initial_pdf_exists(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")

        def side_effect(cmd, **kwargs):
            if cmd[0] == settings.TEX_BIN_PATH:
                (work_dir / "main.aux").write_text(
                    r"\relax" + "\n" + r"\bibdata{refs}",
                    encoding="utf-8",
                )
                (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                return self._tex_success()
            raise FileNotFoundError("bibtex not found")

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is False
            assert result.pdf_path is None
            assert result.error_message == "bibtex binary not found"
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_backend_failure_falls_back_to_backend_error(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")

        def side_effect(cmd, **kwargs):
            if cmd[0] == settings.TEX_BIN_PATH:
                (work_dir / "main.bcf").write_text("bcf", encoding="utf-8")
                (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                return self._tex_success()
            return MagicMock(returncode=1, stdout="INFO - still failing\n")

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is False
            assert result.pdf_path is None
            assert result.error_message == "Biber failed"
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_backend_timeout_returns_timeout(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")

        def side_effect(cmd, **kwargs):
            if cmd[0] == settings.TEX_BIN_PATH:
                (work_dir / "main.bcf").write_text("bcf", encoding="utf-8")
                return self._tex_success()
            raise subprocess.TimeoutExpired(
                cmd="biber",
                timeout=20,
                output="WARN - partial backend warning\n",
            )

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=1, main_file="main.tex", timeout_seconds=20)
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is False
            assert result.error_message == "Compilation timed out"
            assert result.warnings == ["partial backend warning"]
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_first_pass_failure_does_not_run_backend(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        try:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="! LaTeX Error: File `biblatex.sty' not found.\n",
            )

            options = CompileOptions(passes=3, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is False
            assert mock_run.call_count == 1
        finally:
            cleanup_workdir(work_dir)

    @patch("app.services.pipeline.subprocess.run")
    def test_successful_compile_keeps_only_final_tex_warnings(self, mock_run):
        work_dir = self._make_workdir_with_tex(b"\\documentclass{article}")
        calls: list[list[str]] = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == settings.TEX_BIN_PATH:
                pass_number = sum(1 for call in calls if call[0] == settings.TEX_BIN_PATH)
                (work_dir / "main.pdf").write_bytes(b"%PDF-1.4 fake")
                if pass_number == 1:
                    (work_dir / "main.aux").write_text(
                        r"\relax" + "\n" + r"\bibdata{refs}",
                        encoding="utf-8",
                    )
                    return self._tex_success(
                        "LaTeX Warning: Citation `knuth1984' undefined.\n"
                    )
                if pass_number == 2:
                    return self._tex_success(
                        "LaTeX Warning: There were undefined references.\n"
                    )
                return self._tex_success()
            return self._backend_success("Warning--backend note\n")

        mock_run.side_effect = side_effect

        try:
            options = CompileOptions(passes=2, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is True
            assert result.warnings == ["Warning--backend note"]
        finally:
            cleanup_workdir(work_dir)


# =====================================================================
# compile_project — real pdflatex
# =====================================================================


@requires_pdflatex
class TestCompileProjectReal:
    """Integration tests with real pdflatex."""

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
            assert result.pdf_path.read_bytes()[:5] == b"%PDF-"
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


@requires_bibtex
class TestCompileProjectRealBibtex:
    """Integration tests for classic BibTeX projects."""

    def test_classic_bibliography_compile(self):
        work_dir = create_workdir()
        try:
            for rel_path, content in load_fixture_files("with_bib").items():
                safe_write_file(work_dir, rel_path, content)

            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is True
            assert result.pdf_path is not None
            assert not any("undefined" in warning.lower() for warning in result.warnings)
        finally:
            cleanup_workdir(work_dir)


@requires_biblatex
class TestCompileProjectRealBiblatex:
    """Integration tests for biblatex projects."""

    def test_biblatex_bibliography_compile(self):
        work_dir = create_workdir()
        try:
            for rel_path, content in load_fixture_files("with_biblatex").items():
                safe_write_file(work_dir, rel_path, content)

            options = CompileOptions(passes=1, main_file="main.tex")
            result = compile_project(work_dir, "main.tex", options)

            assert result.success is True
            assert result.pdf_path is not None
            assert not any("undefined" in warning.lower() for warning in result.warnings)
        finally:
            cleanup_workdir(work_dir)
