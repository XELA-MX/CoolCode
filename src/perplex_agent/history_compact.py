"""Trim planner history and subagent result blobs to save context tokens."""

from __future__ import annotations


def total_content_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages)


def truncate_middle(text: str, max_len: int) -> str:
    if max_len <= 80 or len(text) <= max_len:
        return text
    head = max_len // 2 - 20
    tail = max_len - head - 25
    omitted = len(text) - head - tail
    return f"{text[:head]}\n… [{omitted} chars omitted] …\n{text[-tail:]}"


def compact_subagent_injections(
    messages: list[dict[str, str]],
    *,
    max_per_message: int,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        c = str(m.get("content", ""))
        if "<subagent_results>" in c and len(c) > max_per_message:
            c = truncate_middle(c, max_per_message)
            out.append({**m, "content": c})
        else:
            out.append(m)
    return out


def trim_planner_history(
    history: list[dict[str, str]],
    *,
    max_total_chars: int,
    inject_max_chars: int,
) -> list[dict[str, str]]:
    """Keep first user turn; drop oldest subsequent turns until under char budget."""
    if not history or max_total_chars <= 0:
        return list(history)
    h = compact_subagent_injections(
        list(history), max_per_message=max(1200, inject_max_chars)
    )
    if total_content_chars(h) <= max_total_chars:
        return h
    if len(h) <= 1:
        return h
    head = [h[0]]
    tail = h[1:]
    while tail and total_content_chars(head + tail) > max_total_chars:
        if len(tail) == 1:
            lone = tail[0]
            content = str(lone.get("content", ""))
            room = max_total_chars - total_content_chars(head) - 80
            room = max(120, room)
            if len(content) > room:
                tail = [{**lone, "content": truncate_middle(content, room)}]
            break
        tail = tail[2:]
    return head + tail
