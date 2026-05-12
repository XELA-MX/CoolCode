"""Workspace path normalization and containment checks."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from perplex_agent.workspace import (
    OutsideWorkspaceError,
    normalize_workspace_root,
    resolve_path_in_workspace,
)


def test_normalize_workspace_root_tmp() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = normalize_workspace_root(td)
        assert root.is_dir()
        assert root == Path(td).resolve()


def test_normalize_workspace_root_rejects_missing() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        normalize_workspace_root("/nonexistent-perplex-agent-workspace-xyz")


def test_resolve_relative_under_root() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = normalize_workspace_root(td)
        (root / "a" / "b").mkdir(parents=True)
        p = resolve_path_in_workspace(root, "a/b")
        assert p == (root / "a" / "b").resolve()


def test_resolve_rejects_parent_escape() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = normalize_workspace_root(td)
        with pytest.raises(OutsideWorkspaceError):
            resolve_path_in_workspace(root, "../outside")
