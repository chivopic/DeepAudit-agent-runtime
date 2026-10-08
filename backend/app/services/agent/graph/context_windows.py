"""Line-ranged source windows and location checks for model findings.

A window is the slice of a file actually sent to the model. Callers record
each window so a report can say which lines were read and which were not.
"""

from __future__ import annotations

from typing import Any


def iter_source_windows(content: str, window_chars: int = 4000) -> list[dict[str, Any]]:
    """Split ``content`` into line ranges that each fit in ``window_chars``."""
    if window_chars < 200:
        window_chars = 200
    lines = content.splitlines()
    if not lines:
        return [
            {
                "start_line": 1,
                "end_line": 1,
                "text": "",
                "truncated": False,
            }
        ]
    windows: list[dict[str, Any]] = []
    start = 1
    buf: list[str] = []
    size = 0
    for index, line in enumerate(lines, start=1):
        piece = len(line) + 1
        if buf and size + piece > window_chars:
            windows.append(
                {
                    "start_line": start,
                    "end_line": index - 1,
                    "text": "\n".join(buf),
                    "truncated": True,
                }
            )
            start = index
            buf = [line]
            size = piece
        else:
            buf.append(line)
            size += piece
    if buf:
        windows.append(
            {
                "start_line": start,
                "end_line": len(lines),
                "text": "\n".join(buf),
                "truncated": len(windows) > 0 or len(content) > window_chars,
            }
        )
    if len(windows) == 1 and len(content) <= window_chars:
        windows[0]["truncated"] = False
    return windows


def validate_model_rows(
    rows: list[dict[str, Any]],
    content: str,
    *,
    path: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep rows whose line exists and whose snippet, if any, is in the file.

    Rows that fail are returned in the second list with a reason. They stay
    candidates-out, never verified findings.
    """
    lines = content.splitlines()
    line_count = len(lines)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            rejected.append({"path": path, "reason": "row_not_object"})
            continue
        try:
            line = int(row.get("line") or 1)
        except (TypeError, ValueError):
            rejected.append({"path": path, "reason": "bad_line"})
            continue
        if line < 1 or line > line_count:
            rejected.append({"path": path, "line": line, "reason": "line_out_of_range"})
            continue
        snippet = row.get("snippet") or row.get("evidence")
        if isinstance(snippet, str) and snippet.strip() and snippet.strip() not in content:
            rejected.append({"path": path, "line": line, "reason": "evidence_not_in_file"})
            continue
        accepted.append(row)
    return accepted, rejected
