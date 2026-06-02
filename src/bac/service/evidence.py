"""Local evidence collection for BAC events."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from bac.core.hash_chain import hash_json, sha256_digest
from bac.service.redaction import redact_data


HUMAN_INPUT_FORMAT = "bac.human_input.v1"
HUMAN_INPUT_CLASSIFICATIONS = {"instruction", "review", "approval"}
HUMAN_INPUT_EVENT_TYPES = {
    "instruction": "human_instruction",
    "review": "human_review",
    "approval": "human_approval",
}
MAX_HUMAN_INPUT_SUMMARY_LENGTH = 96
MAX_HUMAN_INPUT_EXCERPT_LENGTH = 240


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


def normalize_human_input_message(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def human_input_message_hash(text: str) -> str:
    normalized = normalize_human_input_message(text)
    return sha256_digest((HUMAN_INPUT_FORMAT + "\n" + normalized).encode("utf-8"))


def classify_human_input_message(text: str, explicit: str | None = None) -> str:
    if explicit:
        if explicit not in HUMAN_INPUT_CLASSIFICATIONS:
            raise ValueError(f"classification must be one of {sorted(HUMAN_INPUT_CLASSIFICATIONS)}")
        return explicit

    normalized = normalize_human_input_message(text).lower()
    if any(marker in normalized for marker in ("批准", "授权", "approve", "approved")):
        return "approval"
    if any(marker in normalized for marker in ("可以吗", "你觉得呢", "审查", "review", "检查一下", "帮我看")):
        return "review"
    return "instruction"


def build_human_input_evidence(
    *,
    text: str,
    channel: str,
    host: str | None = None,
    session_id: str | None = None,
    message_index: int | None = None,
    classification: str | None = None,
    source_path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    normalized = normalize_human_input_message(text)
    if not normalized:
        raise ValueError("human input message is empty")
    if not channel:
        raise ValueError("human input channel is required")

    resolved_classification = classify_human_input_message(normalized, classification)
    message_hash = human_input_message_hash(normalized)
    summary = _summarize_human_input(normalized)
    excerpt = _excerpt_human_input(normalized)

    provenance: dict[str, Any] = {
        "format": HUMAN_INPUT_FORMAT,
        "channel": channel,
        "message_hash": message_hash,
        "recorded_full_text": False,
        "classification": resolved_classification,
    }
    if host:
        provenance["host"] = host
    if session_id:
        provenance["session_id"] = session_id
    if message_index is not None:
        provenance["message_index"] = message_index
    if source_path:
        provenance["source_path"] = source_path
    if start_line is not None:
        provenance["start_line"] = start_line
    if end_line is not None:
        provenance["end_line"] = end_line

    evidence_type = "prompt_log_block" if channel == "prompt_log" else "human_input_message"
    evidence: dict[str, Any] = {
        "type": evidence_type,
        "message_hash": message_hash,
        "redacted": True,
        "excerpt": excerpt,
    }
    if source_path:
        evidence["source_path"] = source_path
    if start_line is not None:
        evidence["start_line"] = start_line
    if end_line is not None:
        evidence["end_line"] = end_line

    payload = {
        "input_provenance": provenance,
    }
    return summary, payload, [evidence]


def human_input_event_type(classification: str) -> str:
    if classification not in HUMAN_INPUT_EVENT_TYPES:
        raise ValueError(f"classification must be one of {sorted(HUMAN_INPUT_EVENT_TYPES)}")
    return HUMAN_INPUT_EVENT_TYPES[classification]


def parse_prompt_log_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: list[str] = []
    current_start: int | None = None
    in_fence = False

    def flush(end_line: int) -> None:
        nonlocal current, current_start
        block_text = normalize_human_input_message("\n".join(current))
        if block_text and current_start is not None:
            blocks.append({"text": block_text, "start_line": current_start, "end_line": end_line})
        current = []
        current_start = None

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                flush(line_number - 1)
                in_fence = False
            else:
                flush(line_number - 1)
                in_fence = True
            continue
        if in_fence:
            if current_start is None:
                current_start = line_number
            current.append(line)
            continue
        if stripped == "---":
            flush(line_number - 1)
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):
            flush(line_number - 1)
            continue
        if current_start is None:
            current_start = line_number
        current.append(line)

    flush(len(text.splitlines()))
    return blocks


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


def _summarize_human_input(text: str) -> str:
    redacted = _redacted_text(text)
    compact = " ".join(redacted.split())
    if len(compact) <= MAX_HUMAN_INPUT_SUMMARY_LENGTH:
        return compact
    return compact[: MAX_HUMAN_INPUT_SUMMARY_LENGTH - 3].rstrip() + "..."


def _excerpt_human_input(text: str) -> str:
    redacted = _redacted_text(text)
    compact = " ".join(redacted.split())
    if len(compact) <= MAX_HUMAN_INPUT_EXCERPT_LENGTH:
        return compact
    return compact[: MAX_HUMAN_INPUT_EXCERPT_LENGTH - 3].rstrip() + "..."


def _redacted_text(text: str) -> str:
    redacted, _metadata = redact_data(text)
    return redacted if isinstance(redacted, str) else ""
