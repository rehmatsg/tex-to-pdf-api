"""
Unit tests for app.services.textcount.
"""

from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

from app.services.textcount import collect_textcount


def _completed(stdout: str, returncode: int = 0) -> CompletedProcess[str]:
    return CompletedProcess(args=["texcount"], returncode=returncode, stdout=stdout)


@patch("app.services.textcount.subprocess.run")
def test_collect_textcount_ok_with_file_breakdown(mock_run, tmp_path):
    summary = (
        '{"words_text":5,"words_headers":2,"words_captions":0,"headings":1,'
        '"floats":0,"math_inline":0,"math_display":0,"words_total":7,'
        '"errors":0,"warnings":0}\n'
    )
    brief = (
        "5+2+0 (1/0/0/0) File: main.tex\n"
        "3+0+0 (0/0/0/0) Included file: ./chapters/one.tex\n"
    )
    mock_run.side_effect = [_completed(summary), _completed(brief)]

    result = collect_textcount(tmp_path, "main.tex")

    assert result.status == "ok"
    assert result.message is None
    assert result.totals.words_total == 7
    assert result.totals.words_text == 5
    assert result.totals.words_headers == 2
    assert len(result.files) == 2
    assert result.files[0].path == "main.tex"
    assert result.files[0].role == "main"
    assert result.files[1].path == "chapters/one.tex"
    assert result.files[1].role == "included"


@patch("app.services.textcount.subprocess.run")
def test_collect_textcount_unavailable(mock_run, tmp_path):
    mock_run.side_effect = FileNotFoundError("texcount not found")

    result = collect_textcount(tmp_path, "main.tex")

    assert result.status == "unavailable"
    assert "not found" in (result.message or "").lower()
    assert result.totals.words_total == 0
    assert result.files == []


@patch("app.services.textcount.subprocess.run")
def test_collect_textcount_summary_timeout_is_error(mock_run, tmp_path):
    mock_run.side_effect = TimeoutExpired(cmd="texcount", timeout=5)

    result = collect_textcount(tmp_path, "main.tex")

    assert result.status == "error"
    assert "timed out" in (result.message or "").lower()
    assert result.files == []


@patch("app.services.textcount.subprocess.run")
def test_collect_textcount_partial_when_brief_parse_fails(mock_run, tmp_path):
    summary = (
        '{"words_text":6,"words_headers":0,"words_captions":0,"headings":0,'
        '"floats":0,"math_inline":0,"math_display":0,"words_total":6,'
        '"errors":0,"warnings":0}\n'
    )
    bad_brief = "unexpected texcount output\n"
    mock_run.side_effect = [_completed(summary), _completed(bad_brief)]

    result = collect_textcount(tmp_path, "main.tex")

    assert result.status == "partial"
    assert "parse" in (result.message or "").lower()
    assert result.totals.words_total == 6
    assert result.files == []


@patch("app.services.textcount.subprocess.run")
def test_collect_textcount_partial_when_brief_times_out(mock_run, tmp_path):
    summary = (
        '{"words_text":2,"words_headers":0,"words_captions":0,"headings":0,'
        '"floats":0,"math_inline":0,"math_display":0,"words_total":2,'
        '"errors":0,"warnings":0}\n'
    )
    mock_run.side_effect = [_completed(summary), TimeoutExpired(cmd="texcount", timeout=5)]

    result = collect_textcount(tmp_path, "main.tex")

    assert result.status == "partial"
    assert "timed out" in (result.message or "").lower()
    assert result.totals.words_total == 2
