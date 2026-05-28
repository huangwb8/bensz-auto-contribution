"""Local evidence collection for BAC events."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from bac.core.hash_chain import hash_json, sha256_digest


def collect_project_context(root: Path) -> dict[str, Any]:
    repo_root = _git(root, "rev-parse", "--show-toplevel") or str(root.resolve())
    root_path = str(Path(repo_root).resolve())
    git_remote = _git(root, "config", "--get", "remote.origin.url")
    git_commit = _git(root, "rev-parse", "HEAD")
    git_branch = _git(root, "branch", "--show-current")
    dirty = bool(_git(root, "status", "--porcelain"))

    root_hash = hash_json({"root_path": root_path, "git_remote": git_remote})
    return {
        "root_path": root_path,
        "root_hash": root_hash,
        "git_remote": git_remote,
        "git_commit": git_commit,
        "git_branch": git_branch,
        "worktree_dirty": dirty,
    }


def collect_file_snapshots(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    project_root = root.resolve()
    for raw_path in paths:
        resolved = (project_root / raw_path).resolve()
        try:
            relative = resolved.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(f"file path is outside project root: {raw_path}") from exc

        snapshot: dict[str, Any] = {
            "path": relative.as_posix(),
            "exists": resolved.exists(),
            "after_hash": None,
        }
        if resolved.is_file():
            snapshot["after_hash"] = sha256_digest(resolved.read_bytes())
        elif resolved.exists():
            snapshot["kind"] = "non_file"
        snapshots.append(snapshot)
    return snapshots


def collect_git_diff_evidence(root: Path, paths: list[str] | None = None) -> dict[str, Any] | None:
    args = ["diff", "--stat"]
    if paths:
        args.append("--")
        args.extend(paths)
    diff_stat = _git(root, *args)
    if not diff_stat:
        return None
    return {
        "type": "git_diff_summary",
        "hash": sha256_digest(diff_stat.encode("utf-8")),
        "redacted": False,
        "summary": diff_stat,
    }


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
