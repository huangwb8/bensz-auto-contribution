"""Read and append BAC JSON Lines files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bac.core.canonicalize import canonical_json
from bac.core.verify import verify_bac_file


DEFAULT_BAC_FILE = "project.bac"


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
    return events


def current_head_hash(path: Path) -> str | None:
    events = read_events(path)
    if not events:
        return None
    return events[-1].get("event_hash")


def initialize_bac_file(path: Path, genesis_event: dict[str, Any], force: bool = False) -> None:
    if path.exists() and path.read_text(encoding="utf-8").strip() and not force:
        raise FileExistsError(f"BAC file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(genesis_event) + "\n", encoding="utf-8")


def append_event(path: Path, event: dict[str, Any], verify_existing: bool = True) -> None:
    if not path.exists():
        raise FileNotFoundError(f"BAC file does not exist: {path}")
    if verify_existing:
        report = verify_bac_file(path)
        if report.errors:
            raise ValueError(f"cannot append to invalid BAC file: {'; '.join(report.errors)}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(event) + "\n")
