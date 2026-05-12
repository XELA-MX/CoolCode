"""Execute workspace-scoped tools (read_file, list_dir, glob_files)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from perplex_agent.config import Settings
from perplex_agent.workspace import OutsideWorkspaceError, resolve_path_in_workspace


def _safe_int(v: Any, default: int, *, lo: int, hi: int) -> int:
    if v is None:
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _looks_binary_or_mojibake(text: str, sample: int = 4000) -> bool:
    chunk = text[:sample]
    if not chunk:
        return False
    bad = chunk.count("\ufffd")
    return bad / max(len(chunk), 1) > 0.02


def _format_read_file_output(
    path: Path,
    text: str,
    *,
    preview_lines: int,
    truncated_by_bytes: bool,
    channel: str,
) -> str:
    lines = text.splitlines()
    total = len(lines)
    show = max(0, min(preview_lines, total))
    numbered = "\n".join(f"{i + 1:5d} | {lines[i]}" for i in range(show))
    parts: list[str] = [
        f"--- read_file: {path.as_posix()} | {len(text)} chars | {total} line(s) — preview {show}/{total} ---",
        "",
        numbered if numbered else "(empty file)",
    ]
    if total > show:
        parts.append(f"\n… {total - show} more line(s) not shown (preview cap).")
    if truncated_by_bytes:
        parts.append("… truncated by byte limit before line preview.")
    if channel == "telegram":
        parts.append(
            "\n[system] Channel=Telegram: reply with a concise summary and small excerpts only; "
            "do not paste this entire preview back to the user unless they explicitly ask for the full file."
        )
    else:
        parts.append("\n[system] Long files are previewed; cite excerpts in your answer unless the user needs the full text.")
    return "\n".join(parts)


def execute_tool_call(
    settings: Settings,
    call: dict[str, Any],
    *,
    channel: str = "shell",
) -> str:
    """Run one tool call dict (from model JSON). Returns human-readable result or error."""
    name = call.get("name")
    root = settings.workspace_dir
    path_v = call.get("path")
    pattern_v = call.get("pattern")
    path_s = str(path_v).strip() if isinstance(path_v, str) else ""
    pattern_s = str(pattern_v).strip() if isinstance(pattern_v, str) else ""

    try:
        if name == "read_file":
            if not path_s:
                return "error: read_file requires non-empty path"
            target = resolve_path_in_workspace(root, path_s)
            if not target.is_file():
                return f"error: not a file: {target}"

            default_bytes = settings.tool_read_max_bytes
            preview_default = settings.tool_read_preview_lines
            if channel == "telegram":
                default_bytes = min(default_bytes, settings.tool_telegram_read_max_bytes)
                preview_default = min(preview_default, settings.tool_telegram_read_preview_lines)

            max_b = _safe_int(
                call.get("max_bytes"),
                default_bytes,
                lo=256,
                hi=2_000_000,
            )
            preview_lines = max(5, min(2000, preview_default))

            data = target.read_bytes()[:max_b]
            truncated_by_bytes = len(data) >= max_b
            text = data.decode("utf-8", errors="replace")

            if _looks_binary_or_mojibake(text):
                return (
                    f"read_file: {target.as_posix()} — content looks binary or not valid UTF-8 "
                    f"({len(data)} bytes read). Open locally or ask for a specific text range."
                )

            return _format_read_file_output(
                target,
                text,
                preview_lines=preview_lines,
                truncated_by_bytes=truncated_by_bytes,
                channel=channel,
            )

        if name == "list_dir":
            rel = path_s if path_s else "."
            target = resolve_path_in_workspace(root, rel)
            if not target.is_dir():
                return f"error: not a directory: {target}"
            cap = _safe_int(
                call.get("max_entries"),
                settings.tool_list_max_entries,
                lo=1,
                hi=5000,
            )
            names = sorted(target.iterdir(), key=lambda p: p.name.lower())[:cap]
            lines = [f"(listing {target}, max {cap} entries)"]
            for p in names:
                suf = "/" if p.is_dir() else ""
                lines.append(f"{p.name}{suf}")
            if not names:
                lines.append("(empty)")
            return "\n".join(lines)

        if name == "glob_files":
            pat = pattern_s if pattern_s else "*"
            base = resolve_path_in_workspace(root, path_s if path_s else ".")
            if not base.is_dir():
                return f"error: glob base not a directory: {base}"
            cap = _safe_int(
                call.get("max_entries"),
                settings.tool_glob_max_matches,
                lo=1,
                hi=5000,
            )
            root_r = root.resolve()
            matches: list[Path] = []
            try:
                for p in sorted(base.glob(pat), key=lambda x: str(x).lower()):
                    try:
                        p.resolve().relative_to(root_r)
                    except ValueError:
                        continue
                    if p.is_file():
                        matches.append(p)
                    if len(matches) >= cap:
                        break
            except (OSError, ValueError) as e:
                return f"error: glob failed: {e}"
            lines = [f"(glob {pat!r} under {base}, max {cap})"]
            for p in matches[:cap]:
                try:
                    lines.append(str(p.relative_to(root_r)))
                except ValueError:
                    lines.append(str(p))
            if not matches:
                lines.append("(no matches)")
            return "\n".join(lines)

        return f"error: unknown tool {name!r}"
    except OutsideWorkspaceError as e:
        return f"error: {e}"
    except OSError as e:
        return f"error: {e}"


def run_tool_calls(
    settings: Settings,
    calls: list[Any],
    *,
    channel: str = "shell",
) -> str:
    """Execute a batch of tool calls; returns one <tool_results> block."""
    lines: list[str] = ["<tool_results>"]
    if not calls:
        lines.append("(no tool_calls in batch)")
        lines.append("</tool_results>")
        return "\n".join(lines)
    for i, raw in enumerate(calls):
        if not isinstance(raw, dict):
            lines.append(f"## call {i}\nerror: not an object")
            continue
        name = raw.get("name", "?")
        lines.append(f"## {name}")
        lines.append(execute_tool_call(settings, raw, channel=channel))
    lines.append("</tool_results>")
    return "\n".join(lines)
