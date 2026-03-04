"""
Integration tests for the v2 API endpoints.

Uses FastAPI TestClient. Tests that require real pdflatex are marked with
@requires_pdflatex and will be skipped gracefully in CI without TeX.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import (
    HAS_PDFLATEX,
    load_fixture_files,
    make_zip_from_fixture,
    make_zip_from_dict,
    requires_pdflatex,
)

client = TestClient(app)


# =====================================================================
# Health endpoint (v2 format)
# =====================================================================


class TestHealthV2:
    def test_health_returns_v2_format(self):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["version"] == "2.0.0"
        assert isinstance(body["engines"], list)
        # Should not have old field
        assert "tex_available" not in body

    def test_health_has_request_id(self):
        r = client.get("/health")
        assert "x-request-id" in r.headers

    def test_health_echoes_request_id(self):
        r = client.get("/health", headers={"X-Request-Id": "test-123"})
        assert r.headers["x-request-id"] == "test-123"


# =====================================================================
# POST /v2/compile/sync — multi-file compile
# =====================================================================


@requires_pdflatex
class TestV2CompileSync:
    """Integration tests for the multi-file compile endpoint."""

    def test_simple_single_file(self):
        """Test 1: Simple single-file compile."""
        files = load_fixture_files("simple")
        upload_files = [("files", (name, content)) for name, content in files.items()]
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=upload_files,
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:5] == b"%PDF-"
        assert "x-compile-time-ms" in r.headers

    def test_multifile_project(self):
        """Test 2: Multi-file project with chapters."""
        files = load_fixture_files("multifile")
        upload_files = [("files", (name, content)) for name, content in files.items()]
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=upload_files,
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    def test_nested_main_file(self):
        """Test 3: Nested main file path."""
        files = load_fixture_files("nested_main")
        upload_files = [("files", (name, content)) for name, content in files.items()]
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "src/main.tex"},
            files=upload_files,
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    def test_with_custom_sty(self):
        """Test 5: Project with custom .sty package."""
        files = load_fixture_files("with_sty")
        upload_files = [("files", (name, content)) for name, content in files.items()]
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=upload_files,
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    def test_return_json_format(self):
        """Test 9: return=json returns JSON with logs."""
        files = load_fixture_files("simple")
        upload_files = [("files", (name, content)) for name, content in files.items()]
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex", "return": "json"},
            files=upload_files,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "pdf_base64" in body
        assert "compile_time_ms" in body
        assert isinstance(body["errors"], list)
        assert isinstance(body["warnings"], list)
        assert "log" in body
        assert "textcount" in body
        assert body["textcount"]["status"] in ("ok", "partial", "unavailable", "error")
        assert isinstance(body["textcount"]["totals"], dict)
        assert isinstance(body["textcount"]["files"], list)

    def test_compile_error_returns_400(self):
        """Test 7: Compile error returns 400 with structured error."""
        bad_tex = rb"""\documentclass{article}
\begin{document}
\undefinedcommandxyz
\end{document}
"""
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("main.tex", bad_tex))],
        )
        assert r.status_code == 400
        body = r.json()
        assert body["status"] == "error"
        assert body["error_type"] in ("latex_compile_error", "timeout")
        assert "log" in body

    def test_missing_main_file_returns_422(self):
        """Test 8: Missing main_file returns 422."""
        simple_tex = rb"\documentclass{article}\begin{document}Hi\end{document}"
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "nonexistent.tex"},
            files=[("files", ("main.tex", simple_tex))],
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_type"] == "invalid_input"
        assert "nonexistent.tex" in body["message"]

    def test_unsupported_engine_returns_422(self):
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex", "engine": "luatex"},
            files=[("files", ("main.tex", b"content"))],
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_type"] == "invalid_input"
        assert "engine" in body["message"].lower()


# =====================================================================
# POST /v2/compile/zip — zip compile
# =====================================================================


@requires_pdflatex
class TestV2CompileZip:
    """Integration tests for the zip compile endpoint."""

    def test_zip_compile(self):
        """Test 6: Zip compile works end-to-end."""
        zip_bytes = make_zip_from_fixture("simple")
        r = client.post(
            "/v2/compile/zip",
            data={"main_file": "main.tex"},
            files=[("file", ("project.zip", zip_bytes, "application/zip"))],
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    def test_zip_multifile_project(self):
        zip_bytes = make_zip_from_fixture("multifile")
        r = client.post(
            "/v2/compile/zip",
            data={"main_file": "main.tex"},
            files=[("file", ("project.zip", zip_bytes, "application/zip"))],
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"

    def test_zip_missing_main_file(self):
        zip_bytes = make_zip_from_fixture("simple")
        r = client.post(
            "/v2/compile/zip",
            data={"main_file": "nonexistent.tex"},
            files=[("file", ("project.zip", zip_bytes, "application/zip"))],
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_type"] == "invalid_input"

    def test_zip_return_json(self):
        zip_bytes = make_zip_from_fixture("simple")
        r = client.post(
            "/v2/compile/zip",
            data={"main_file": "main.tex", "return": "json"},
            files=[("file", ("project.zip", zip_bytes, "application/zip"))],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "pdf_base64" in body
        assert "textcount" in body
        assert body["textcount"]["status"] in ("ok", "partial", "unavailable", "error")


# =====================================================================
# POST /v2/compile/validate
# =====================================================================


class TestV2Validate:
    """Integration tests for the validate endpoint."""

    @requires_pdflatex
    def test_valid_code(self):
        r = client.post(
            "/v2/compile/validate",
            json={
                "code": r"\documentclass{article}\begin{document}Hello\end{document}"
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["compilable"] is True
        assert isinstance(body["errors"], list)
        assert isinstance(body["warnings"], list)
        assert "compile_time_ms" in body

    @requires_pdflatex
    def test_invalid_code(self):
        r = client.post(
            "/v2/compile/validate",
            json={
                "code": r"\documentclass{article}\begin{document}\unknowncmd\end{document}"
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["compilable"] is False
        assert len(body["errors"]) > 0

    def test_empty_code(self):
        r = client.post("/v2/compile/validate", json={"code": ""})
        assert r.status_code == 422
        body = r.json()
        assert body["error_type"] == "invalid_input"

    def test_whitespace_only_code(self):
        r = client.post("/v2/compile/validate", json={"code": "   "})
        assert r.status_code == 422

    def test_dangerous_macro_rejected(self):
        r = client.post(
            "/v2/compile/validate",
            json={"code": r"\write18{rm -rf /}"},
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_type"] == "invalid_input"
        assert "Dangerous" in body["message"] or "macro" in body["message"].lower()


# =====================================================================
# Response headers
# =====================================================================


class TestResponseHeaders:
    """Verify standard response headers across v2 endpoints."""

    def test_request_id_on_v2_validate(self):
        r = client.post("/v2/compile/validate", json={"code": ""})
        assert "x-request-id" in r.headers

    @requires_pdflatex
    def test_request_id_on_v2_compile(self):
        files = load_fixture_files("simple")
        upload_files = [("files", (name, content)) for name, content in files.items()]
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=upload_files,
        )
        assert "x-request-id" in r.headers
