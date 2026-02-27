"""
Security tests for the v2 API.

Tests that malicious inputs are properly rejected. These tests do NOT require
pdflatex since they should all fail validation before reaching the compiler.
"""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_zip_from_dict

client = TestClient(app)

SAFE_TEX = rb"\documentclass{article}\begin{document}Hi\end{document}"


# =====================================================================
# Path traversal attacks via /v2/compile/sync
# =====================================================================


class TestPathTraversal:
    """Test that path traversal attempts are rejected."""

    def test_dotdot_path(self):
        """Security 1: ../evil.tex rejected."""
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("../evil.tex", SAFE_TEX))],
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_type"] == "invalid_input"

    def test_absolute_path(self):
        """Security 2: /etc/passwd rejected."""
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("/etc/passwd", SAFE_TEX))],
        )
        assert r.status_code == 422

    def test_backslash_path(self):
        """Security 3: src\\main.tex rejected."""
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("src\\main.tex", SAFE_TEX))],
        )
        assert r.status_code == 422

    def test_null_byte_in_path(self):
        """Security 4: Null byte in path rejected."""
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("main\x00.tex", SAFE_TEX))],
        )
        assert r.status_code == 422

    def test_path_too_long(self):
        """Security 12: Path > 300 chars rejected."""
        long_name = "a" * 297 + ".tex"  # 301 chars
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", (long_name, SAFE_TEX))],
        )
        assert r.status_code == 422


# =====================================================================
# File type restrictions
# =====================================================================


class TestFileTypeRestriction:
    """Test that disallowed file types are rejected."""

    def test_shell_script_rejected(self):
        """Security 11: Non-whitelisted file type rejected."""
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[
                ("files", ("main.tex", SAFE_TEX)),
                ("files", ("evil.sh", b"#!/bin/bash\nrm -rf /")),
            ],
        )
        assert r.status_code == 422
        body = r.json()
        assert "not allowed" in body["message"]

    def test_exe_rejected(self):
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[
                ("files", ("main.tex", SAFE_TEX)),
                ("files", ("malware.exe", b"\x00" * 100)),
            ],
        )
        assert r.status_code == 422

    def test_no_extension_rejected(self):
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[
                ("files", ("main.tex", SAFE_TEX)),
                ("files", ("Makefile", b"all: build")),
            ],
        )
        assert r.status_code == 422


# =====================================================================
# Resource limits
# =====================================================================


class TestResourceLimits:
    """Test that resource limits are enforced."""

    def test_too_many_files(self):
        """Security 5: 501 files rejected."""
        upload_files = [("files", ("main.tex", SAFE_TEX))]
        for i in range(500):
            upload_files.append(("files", (f"file_{i}.tex", b"% dummy")))

        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=upload_files,
        )
        # 501 files should trigger rejection (413 for payload_too_large or 422)
        assert r.status_code in (413, 422)

    def test_total_size_too_large(self):
        """Security 6: Total size > 20MB rejected."""
        big_content = b"x" * (21 * 1024 * 1024)  # 21MB
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("main.tex", big_content))],
        )
        assert r.status_code == 413

    def test_invalid_passes_zero(self):
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex", "passes": "0"},
            files=[("files", ("main.tex", SAFE_TEX))],
        )
        assert r.status_code == 422

    def test_invalid_passes_six(self):
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex", "passes": "6"},
            files=[("files", ("main.tex", SAFE_TEX))],
        )
        assert r.status_code == 422


# =====================================================================
# Dangerous macros via API
# =====================================================================


class TestDangerousMacrosAPI:
    """Test that dangerous macros are blocked at the API level."""

    def test_write18_in_tex(self):
        """Security 7: Dangerous macro in .tex rejected."""
        evil_tex = rb"""\documentclass{article}
\write18{cat /etc/passwd}
\begin{document}
Hello
\end{document}
"""
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("main.tex", evil_tex))],
        )
        assert r.status_code == 422
        body = r.json()
        assert "Dangerous" in body["message"] or "macro" in body["message"].lower()

    def test_write18_in_sty(self):
        """Security 8: Dangerous macro in .sty rejected."""
        evil_sty = rb"\ProvidesPackage{evil}" + b"\n" + rb"\immediate\write18{ls}"
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[
                ("files", ("main.tex", SAFE_TEX)),
                ("files", ("evil.sty", evil_sty)),
            ],
        )
        assert r.status_code == 422

    def test_openout_in_cls(self):
        evil_cls = rb"\ProvidesClass{evil}" + b"\n" + rb"\openout\myfile=output.txt"
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[
                ("files", ("main.tex", SAFE_TEX)),
                ("files", ("evil.cls", evil_cls)),
            ],
        )
        assert r.status_code == 422

    def test_input_pipe(self):
        evil = rb'\documentclass{article}\input|"ls"\begin{document}\end{document}'
        r = client.post(
            "/v2/compile/sync",
            data={"main_file": "main.tex"},
            files=[("files", ("main.tex", evil))],
        )
        assert r.status_code == 422


# =====================================================================
# Zip security
# =====================================================================


class TestZipSecurity:
    """Test that zip-specific attacks are blocked."""

    def test_zip_path_traversal(self):
        """Security 9: Zip slip attempt rejected."""
        # Create a zip with a traversal path
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../../etc/evil.tex", SAFE_TEX.decode())
        zip_bytes = buf.getvalue()

        r = client.post(
            "/v2/compile/zip",
            data={"main_file": "main.tex"},
            files=[("file", ("project.zip", zip_bytes, "application/zip"))],
        )
        assert r.status_code == 422

    def test_zip_absolute_path(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("/etc/passwd", "root:x:0:0:")
        zip_bytes = buf.getvalue()

        r = client.post(
            "/v2/compile/zip",
            data={"main_file": "main.tex"},
            files=[("file", ("project.zip", zip_bytes, "application/zip"))],
        )
        assert r.status_code == 422

    def test_zip_disallowed_file_type(self):
        zip_bytes = make_zip_from_dict(
            {
                "main.tex": SAFE_TEX,
                "exploit.sh": b"#!/bin/bash",
            }
        )
        r = client.post(
            "/v2/compile/zip",
            data={"main_file": "main.tex"},
            files=[("file", ("project.zip", zip_bytes, "application/zip"))],
        )
        assert r.status_code == 422

    def test_zip_dangerous_macro(self):
        evil = (
            rb"\documentclass{article}\write18{rm -rf /}\begin{document}\end{document}"
        )
        zip_bytes = make_zip_from_dict({"main.tex": evil})
        r = client.post(
            "/v2/compile/zip",
            data={"main_file": "main.tex"},
            files=[("file", ("project.zip", zip_bytes, "application/zip"))],
        )
        assert r.status_code == 422


# =====================================================================
# Validate endpoint security
# =====================================================================


class TestValidateSecurity:
    def test_validate_rejects_dangerous_macros(self):
        r = client.post(
            "/v2/compile/validate",
            json={"code": r"\write18{rm -rf /}"},
        )
        assert r.status_code == 422

    def test_validate_rejects_invalid_passes(self):
        r = client.post(
            "/v2/compile/validate",
            json={
                "code": r"\documentclass{article}\begin{document}Hi\end{document}",
                "passes": 10,
            },
        )
        assert r.status_code == 422
