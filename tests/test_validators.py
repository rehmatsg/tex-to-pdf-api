"""
Unit tests for app.services.validators.

Covers:
- File path validation (safe paths, traversal, absolute, backslash, null, length)
- File extension whitelisting
- Dangerous macro scanning
- Resource limit enforcement
"""

import pytest

from app.services.validators import (
    ALLOWED_EXTENSIONS,
    PayloadTooLargeError,
    ValidationError,
    scan_dangerous_macros,
    validate_file_extension,
    validate_file_path,
    validate_limits,
)


# =====================================================================
# validate_file_path
# =====================================================================


class TestValidateFilePath:
    """Tests for validate_file_path()."""

    # --- valid paths ---

    def test_simple_filename(self):
        assert validate_file_path("main.tex") == "main.tex"

    def test_nested_path(self):
        assert validate_file_path("src/main.tex") == "src/main.tex"

    def test_deeply_nested(self):
        assert validate_file_path("a/b/c/d/e.tex") == "a/b/c/d/e.tex"

    def test_image_in_subdir(self):
        assert validate_file_path("figures/diagram.png") == "figures/diagram.png"

    # --- rejected paths ---

    def test_empty_string(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            validate_file_path("")

    def test_whitespace_only(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            validate_file_path("   ")

    def test_null_byte(self):
        with pytest.raises(ValidationError, match="null bytes"):
            validate_file_path("main\x00.tex")

    def test_absolute_path(self):
        with pytest.raises(ValidationError, match="relative"):
            validate_file_path("/etc/passwd")

    def test_dotdot_traversal(self):
        with pytest.raises(ValidationError, match="\\.\\."):
            validate_file_path("../evil.tex")

    def test_dotdot_in_middle(self):
        with pytest.raises(ValidationError, match="\\.\\."):
            validate_file_path("src/../../../etc/passwd")

    def test_backslash(self):
        with pytest.raises(ValidationError, match="backslash"):
            validate_file_path("src\\main.tex")

    def test_windows_absolute(self):
        with pytest.raises(ValidationError, match="backslash"):
            validate_file_path("C:\\windows\\foo")

    def test_too_long_path(self):
        long_path = "a/" * 150 + "x.tex"  # > 300 chars
        with pytest.raises(ValidationError, match="maximum length"):
            validate_file_path(long_path)

    def test_exactly_300_chars(self):
        # Build a path that is exactly 300 chars and valid
        name = "a" * 296 + ".tex"  # 300 chars total
        assert len(name) == 300
        # Should NOT raise
        validate_file_path(name)

    def test_301_chars(self):
        name = "a" * 297 + ".tex"  # 301 chars
        assert len(name) == 301
        with pytest.raises(ValidationError, match="maximum length"):
            validate_file_path(name)


# =====================================================================
# validate_file_extension
# =====================================================================


class TestValidateFileExtension:
    """Tests for validate_file_extension()."""

    @pytest.mark.parametrize(
        "filename",
        [
            "main.tex",
            "refs.bib",
            "style.bst",
            "my.cls",
            "pkg.sty",
            "image.png",
            "photo.jpg",
            "photo.jpeg",
            "doc.pdf",
            "data.txt",
            "data.csv",
            "figure.eps",
            "diagram.svg",
        ],
    )
    def test_allowed_extensions(self, filename):
        # Should not raise
        validate_file_extension(filename)

    @pytest.mark.parametrize(
        "filename",
        [
            "script.sh",
            "malware.exe",
            "lib.dll",
            "binary.bin",
            "archive.zip",
            "archive.tar",
            "archive.gz",
            "config.json",
            "prog.py",
            "data.xml",
        ],
    )
    def test_rejected_extensions(self, filename):
        with pytest.raises(ValidationError, match="not allowed"):
            validate_file_extension(filename)

    def test_no_extension(self):
        with pytest.raises(ValidationError, match="no extension"):
            validate_file_extension("Makefile")

    def test_case_insensitive(self):
        # .TEX should be accepted (extension check is case-insensitive)
        validate_file_extension("MAIN.TEX")

    def test_mixed_case(self):
        validate_file_extension("photo.JPG")


# =====================================================================
# scan_dangerous_macros
# =====================================================================


class TestScanDangerousMacros:
    """Tests for scan_dangerous_macros()."""

    def test_clean_tex_file(self):
        content = rb"""\documentclass{article}
\usepackage{amsmath}
\begin{document}
Hello $x^2$
\end{document}
"""
        # Should not raise
        scan_dangerous_macros(content, "main.tex")

    def test_write18_in_tex(self):
        content = rb"\write18{rm -rf /}"
        with pytest.raises(ValidationError, match="Dangerous macro"):
            scan_dangerous_macros(content, "main.tex")

    def test_immediate_write18_in_sty(self):
        content = rb"\immediate\write18{ls}"
        with pytest.raises(ValidationError, match="Dangerous macro"):
            scan_dangerous_macros(content, "custom.sty")

    def test_openout_in_cls(self):
        content = rb"\openout\myfile=output.txt"
        with pytest.raises(ValidationError, match="Dangerous macro"):
            scan_dangerous_macros(content, "myclass.cls")

    def test_openin(self):
        content = rb"\openin\myread=/etc/passwd"
        with pytest.raises(ValidationError, match="Dangerous macro"):
            scan_dangerous_macros(content, "evil.tex")

    def test_input_pipe(self):
        content = rb'\input|"ls -la"'
        with pytest.raises(ValidationError, match="Dangerous macro"):
            scan_dangerous_macros(content, "evil.tex")

    def test_newwrite(self):
        content = rb"\newwrite\myfile"
        with pytest.raises(ValidationError, match="Dangerous macro"):
            scan_dangerous_macros(content, "evil.tex")

    def test_newread(self):
        content = rb"\newread\myread"
        with pytest.raises(ValidationError, match="Dangerous macro"):
            scan_dangerous_macros(content, "evil.tex")

    def test_non_scannable_extension_ignored(self):
        # .png files are not scanned even if they contain "dangerous" strings
        content = rb"\write18{rm -rf /}"
        scan_dangerous_macros(content, "image.png")

    def test_bib_not_scanned(self):
        content = rb"\write18{exploit}"
        scan_dangerous_macros(content, "refs.bib")

    def test_non_utf8_tex_rejected(self):
        # Invalid UTF-8 in a .tex file should be rejected
        content = b"\x80\x81\x82\x83"
        with pytest.raises(ValidationError, match="UTF-8"):
            scan_dangerous_macros(content, "bad.tex")

    def test_non_utf8_sty_rejected(self):
        content = b"\xff\xfe"
        with pytest.raises(ValidationError, match="UTF-8"):
            scan_dangerous_macros(content, "bad.sty")

    def test_non_utf8_png_ok(self):
        # Binary files with non-scannable extensions should pass
        content = b"\x89PNG\r\n\x1a\n\x00\x00"
        scan_dangerous_macros(content, "image.png")


# =====================================================================
# validate_limits
# =====================================================================


class TestValidateLimits:
    """Tests for validate_limits()."""

    def test_within_limits(self):
        # Should not raise
        validate_limits(file_count=10, total_bytes=1024, passes=2)

    def test_single_file_single_pass(self):
        validate_limits(file_count=1, total_bytes=100, passes=1)

    def test_max_passes(self):
        validate_limits(file_count=1, total_bytes=100, passes=5)

    def test_too_many_files(self):
        with pytest.raises(PayloadTooLargeError, match="Too many files"):
            validate_limits(file_count=501, total_bytes=100, passes=1)

    def test_exactly_500_files(self):
        # Should not raise
        validate_limits(file_count=500, total_bytes=100, passes=1)

    def test_total_bytes_too_large(self):
        with pytest.raises(PayloadTooLargeError, match="exceeds maximum"):
            validate_limits(file_count=1, total_bytes=21 * 1024 * 1024, passes=1)

    def test_passes_zero(self):
        with pytest.raises(ValidationError, match="Passes must be between"):
            validate_limits(file_count=1, total_bytes=100, passes=0)

    def test_passes_six(self):
        with pytest.raises(ValidationError, match="Passes must be between"):
            validate_limits(file_count=1, total_bytes=100, passes=6)

    def test_passes_negative(self):
        with pytest.raises(ValidationError, match="Passes must be between"):
            validate_limits(file_count=1, total_bytes=100, passes=-1)
