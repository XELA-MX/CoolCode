"""Workspace root: anchor for future file/shell tools (paths must stay under this directory)."""

from __future__ import annotations

from pathlib import Path


class OutsideWorkspaceError(ValueError):
    """Resolved path escapes the workspace root (e.g. via .. or symlinks)."""


def normalize_workspace_root(raw: Path | str) -> Path:
    """Return absolute, resolved workspace directory; must exist and be a directory."""
    p = Path(raw).expanduser()
    try:
        p = p.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Cannot resolve workspace path {raw!r}: {e}") from e
    if not p.exists():
        raise ValueError(f"Workspace does not exist: {p}")
    if not p.is_dir():
        raise ValueError(f"Workspace is not a directory: {p}")
    return p


def resolve_path_in_workspace(workspace: Path, candidate: Path | str) -> Path:
    """Resolve *candidate* to an absolute path that must lie under *workspace*.

    Relative paths are interpreted relative to *workspace*. Absolute paths are
    allowed only if they are inside *workspace* after full resolution (including
    symlinks).
    """
    root = normalize_workspace_root(workspace)
    c = Path(candidate).expanduser()
    if c.is_absolute():
        resolved = c.resolve(strict=False)
    else:
        resolved = (root / c).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as e:
        raise OutsideWorkspaceError(
            f"Path {candidate!r} resolves to {resolved}, outside workspace {root}"
        ) from e
    return resolved
