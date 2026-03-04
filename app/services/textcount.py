"""
Structured texcount integration for v2 compile JSON responses.

This module is intentionally best-effort:
- It never raises into API handlers.
- It reports failures through `TextCountResponse.status/message`.
"""

import json
import logging
import re
import subprocess
from pathlib import Path

from app.core.config import settings
from app.models.compile import (
    TextCountFileBreakdown,
    TextCountResponse,
    TextCountTotals,
)

logger = logging.getLogger(__name__)

_SUMMARY_TEMPLATE = (
    '{"words_text":{text},"words_headers":{headerword},'
    '"words_captions":{otherword},"headings":{header},"floats":{float},'
    '"math_inline":{inlinemath},"math_display":{displaymath},'
    '"words_total":{sum},"errors":{errors},"warnings":{warnings}}'
)

_BRIEF_LINE_RE = re.compile(
    r"^\s*(?P<text>\d+)\+(?P<headers>\d+)\+(?P<captions>\d+)\s+"
    r"\((?P<headings>\d+)/(?P<floats>\d+)/(?P<inline>\d+)/(?P<display>\d+)\)\s+"
    r"(?P<label>File|Included file):\s+(?P<path>.+?)\s*$"
)


def collect_textcount(work_dir: Path, main_file: str) -> TextCountResponse:
    """
    Collect structured texcount metadata for a compiled project.

    This function is soft-fail by design: it always returns a TextCountResponse
    and does not raise.
    """
    summary_cmd = [
        settings.TEXTCOUNT_BIN_PATH,
        "-inc",
        "-sum",
        f"-template={_SUMMARY_TEMPLATE}",
        main_file,
    ]

    try:
        summary_run = _run_texcount(summary_cmd, work_dir)
    except FileNotFoundError:
        return TextCountResponse(
            status="unavailable",
            message=f"{settings.TEXTCOUNT_BIN_PATH!r} binary not found",
        )
    except subprocess.TimeoutExpired:
        return TextCountResponse(
            status="error",
            message=(
                "texcount summary timed out "
                f"after {settings.TEXTCOUNT_TIMEOUT_SECONDS}s"
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive catch
        logger.exception("Unexpected texcount summary failure")
        return TextCountResponse(status="error", message=f"texcount failed: {exc}")

    summary_data = _extract_summary_json(summary_run.stdout or "")
    if summary_data is None:
        return TextCountResponse(
            status="error",
            message="Could not parse texcount summary output",
        )
    totals = _build_totals(summary_data)

    brief_cmd = [settings.TEXTCOUNT_BIN_PATH, "-inc", "-brief", main_file]
    try:
        brief_run = _run_texcount(brief_cmd, work_dir)
    except FileNotFoundError:
        return TextCountResponse(
            status="partial",
            message=f"{settings.TEXTCOUNT_BIN_PATH!r} became unavailable",
            totals=totals,
        )
    except subprocess.TimeoutExpired:
        return TextCountResponse(
            status="partial",
            message=(
                "texcount per-file breakdown timed out "
                f"after {settings.TEXTCOUNT_TIMEOUT_SECONDS}s"
            ),
            totals=totals,
        )
    except Exception as exc:  # pragma: no cover - defensive catch
        logger.exception("Unexpected texcount file breakdown failure")
        return TextCountResponse(
            status="partial",
            message=f"texcount file breakdown failed: {exc}",
            totals=totals,
        )

    files, parse_error = _parse_brief_output(brief_run.stdout or "")
    if parse_error:
        status_msg = parse_error
        if brief_run.returncode != 0:
            status_msg += " (non-zero exit status)"
        return TextCountResponse(
            status="partial",
            message=status_msg,
            totals=totals,
        )

    # If texcount output is unexpectedly empty, treat it as a valid empty
    # file breakdown instead of failing the whole metadata payload.
    if not files:
        return TextCountResponse(status="ok", totals=totals, files=[])

    files = _ensure_main_first(files, main_file)
    return TextCountResponse(status="ok", totals=totals, files=files)


def _run_texcount(cmd: list[str], work_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(work_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=settings.TEXTCOUNT_TIMEOUT_SECONDS,
        check=False,
    )


def _extract_summary_json(output: str) -> dict | None:
    # texcount may emit warning lines before the template output.
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{") or not candidate.endswith("}"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _build_totals(data: dict) -> TextCountTotals:
    return TextCountTotals(
        words_total=_as_int(data.get("words_total")),
        words_text=_as_int(data.get("words_text")),
        words_headers=_as_int(data.get("words_headers")),
        words_captions=_as_int(data.get("words_captions")),
        headings=_as_int(data.get("headings")),
        floats=_as_int(data.get("floats")),
        math_inline=_as_int(data.get("math_inline")),
        math_display=_as_int(data.get("math_display")),
    )


def _parse_brief_output(
    output: str,
) -> tuple[list[TextCountFileBreakdown], str | None]:
    rows: list[TextCountFileBreakdown] = []

    for line in output.splitlines():
        m = _BRIEF_LINE_RE.match(line)
        if not m:
            continue

        words_text = int(m.group("text"))
        words_headers = int(m.group("headers"))
        words_captions = int(m.group("captions"))
        headings = int(m.group("headings"))
        floats = int(m.group("floats"))
        math_inline = int(m.group("inline"))
        math_display = int(m.group("display"))
        raw_path = m.group("path").strip()
        path = raw_path[2:] if raw_path.startswith("./") else raw_path
        role = "main" if m.group("label") == "File" else "included"

        rows.append(
            TextCountFileBreakdown(
                path=path,
                role=role,
                words_total=(
                    words_text
                    + words_headers
                    + words_captions
                    + math_inline
                    + math_display
                ),
                words_text=words_text,
                words_headers=words_headers,
                words_captions=words_captions,
                headings=headings,
                floats=floats,
                math_inline=math_inline,
                math_display=math_display,
            )
        )

    if rows:
        return rows, None
    if not output.strip():
        return [], None
    return [], "Could not parse per-file texcount output"


def _ensure_main_first(
    files: list[TextCountFileBreakdown], main_file: str
) -> list[TextCountFileBreakdown]:
    main_candidates = [
        f
        for f in files
        if f.role == "main"
        and (f.path == main_file or f.path.endswith(f"/{main_file}".lstrip("/")))
    ]
    if main_candidates:
        main_entry = main_candidates[0]
        rest = [f for f in files if f is not main_entry]
        return [main_entry, *rest]
    return files


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
