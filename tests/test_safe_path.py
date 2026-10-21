"""Regression tests for safe_path workspace containment (cross-platform).

The original implementation compared `str(target).startswith(str(root) + "/")`,
which is wrong on Windows (backslash separators) and rejected every valid
relative path. These tests pin down the correct behavior on any OS.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.errors import ToolError
from services.tools.registry import safe_path


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "app" / "routers").mkdir(parents=True)
    (ws / "app" / "routers" / "users.py").write_text("x = 1\n")
    (ws / "app" / "main.py").write_text("y = 2\n")
    return ws


def test_relative_forward_slashes(workspace: Path) -> None:
    p = safe_path(workspace, "app/routers/users.py")
    assert p == (workspace / "app" / "routers" / "users.py").resolve()


def test_relative_single_segment(workspace: Path) -> None:
    p = safe_path(workspace, "app/main.py")
    assert p == (workspace / "app" / "main.py").resolve()


def test_backslash_paths_stay_inside_workspace(workspace: Path) -> None:
    # On Windows the LLM/tool layer may emit backslash paths; they must
    # resolve to the real file inside the workspace. On POSIX a backslash is
    # just a filename character, so the result is a (nonexistent) file that
    # is still safely inside the workspace.
    p = safe_path(workspace, "app\\routers\\users.py")
    if os.name == "nt":
        assert p == (workspace / "app" / "routers" / "users.py").resolve()
    else:
        assert p.is_relative_to(workspace.resolve())


def test_traversal_rejected(workspace: Path) -> None:
    with pytest.raises(ToolError):
        safe_path(workspace, "../outside.py")


def test_traversal_nested_rejected(workspace: Path) -> None:
    with pytest.raises(ToolError):
        safe_path(workspace, "app/../../outside.py")


def test_absolute_path_rejected(workspace: Path) -> None:
    with pytest.raises(ToolError):
        safe_path(workspace, "/etc/passwd")


def test_home_path_rejected(workspace: Path) -> None:
    with pytest.raises(ToolError):
        safe_path(workspace, "~/secret.txt")


def test_empty_path_rejected(workspace: Path) -> None:
    with pytest.raises(ToolError):
        safe_path(workspace, "")


def test_sibling_prefix_dir_not_confused(workspace: Path, tmp_path: Path) -> None:
    # `ws-evil` is a sibling of `ws`; the old startswith check would have
    # accepted "ws-evil/x.py" as being inside "ws". is_relative_to must not.
    evil = tmp_path / "ws-evil"
    evil.mkdir()
    (evil / "x.py").write_text("evil\n")
    with pytest.raises(ToolError):
        safe_path(workspace, "../ws-evil/x.py")


def test_workspace_root_itself_allowed(workspace: Path) -> None:
    # Resolving to the workspace root itself is not an escape.
    p = safe_path(workspace, ".")
    assert p == workspace.resolve()