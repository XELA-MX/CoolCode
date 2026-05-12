"""Workspace tool execution."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from perplex_agent.config import Settings
from perplex_agent.tool_runtime import execute_tool_call, run_tool_calls


def _settings(root: Path) -> Settings:
    return Settings(perplexity_api_key="x", workspace_dir=root)


def test_read_file_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        (root / "hello.txt").write_text("abc", encoding="utf-8")
        s = _settings(root)
        out = execute_tool_call(
            s,
            {
                "name": "read_file",
                "path": "hello.txt",
                "pattern": None,
                "max_bytes": None,
                "max_entries": None,
            },
        )
        assert "read_file:" in out or "hello.txt" in out
        assert "abc" in out


def test_list_dir() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        (root / "sub").mkdir()
        s = _settings(root)
        out = execute_tool_call(
            s,
            {
                "name": "list_dir",
                "path": ".",
                "pattern": None,
                "max_bytes": None,
                "max_entries": 10,
            },
        )
        assert "sub" in out


def test_run_tool_calls_glob() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        (root / "a.py").write_text("#", encoding="utf-8")
        s = _settings(root)
        block = run_tool_calls(
            s,
            [
                {
                    "name": "glob_files",
                    "path": None,
                    "pattern": "*.py",
                    "max_bytes": None,
                    "max_entries": 20,
                }
            ],
        )
        assert "<tool_results>" in block
        assert "a.py" in block
