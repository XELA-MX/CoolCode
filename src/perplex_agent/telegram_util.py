"""Helpers for Telegram message size limits."""

from __future__ import annotations

# Below Telegram hard limit (4096) to leave room for formatting.
CHUNK_SIZE = 3500


def split_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
