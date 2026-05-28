"""BAC ledger verification."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bac.core.hash_chain import compute_event_hash
from bac.core.schema import parse_created_at, validate_event_schema


@dataclass
class VerificationReport:
    status: str
    checked_events: int = 0
    head_hash: str | None = None
    signature_status: str = "unsigned"
    anchor_status: str = "not_anchored"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checked_events": self.checked_events,
            "head_hash": self.head_hash,
            "signature_status": self.signature_status,
            "anchor_status": self.anchor_status,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def verify_bac_file(path: Path) -> VerificationReport:
    if not path.exists():
        return VerificationReport(status="fail", errors=[f"BAC file does not exist: {path}"])

    events: list[Any] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if isinstance(value, dict):
            value["_bac_line_number"] = line_number
        events.append(value)

    report = verify_events(events)
    report.errors = errors + report.errors
    report.status = _status(report.errors, report.warnings)
    return report


def verify_events(events: list[Any]) -> VerificationReport:
    warnings: list[str] = []
    errors: list[str] = []
    previous_hash: str | None = None
    previous_created_at = None
    project_root_hash: str | None = None
    signed_count = 0
    checkpoint_count = 0

    if not events:
        return VerificationReport(status="fail", errors=["BAC file contains no events"])

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.extend(validate_event_schema(event, index + 1))
            continue

        line_number = event.pop("_bac_line_number", None)
        errors.extend(validate_event_schema(event, line_number))

        event_id = event.get("event_id", f"#{index + 1}")
        expected_hash = compute_event_hash(event)
        if event.get("event_hash") != expected_hash:
            errors.append(f"event {event_id}: event_hash mismatch")

        if index == 0:
            if event.get("event_type") != "genesis":
                errors.append("first event must be genesis")
            if event.get("prev_event_hash") is not None:
                errors.append("genesis event prev_event_hash must be null")
        elif event.get("prev_event_hash") != previous_hash:
            errors.append(f"event {event_id}: prev_event_hash does not match previous event_hash")

        project = event.get("project")
        if isinstance(project, dict):
            current_root_hash = project.get("root_hash")
            if project_root_hash is None:
                project_root_hash = current_root_hash
            elif current_root_hash != project_root_hash:
                errors.append(f"event {event_id}: project.root_hash changed within ledger")

        created_at = event.get("created_at")
        if isinstance(created_at, str) and created_at.endswith("Z"):
            try:
                parsed_created_at = parse_created_at(created_at)
            except ValueError:
                parsed_created_at = None
            if previous_created_at and parsed_created_at and parsed_created_at < previous_created_at:
                warnings.append(f"event {event_id}: created_at is earlier than previous event")
            if parsed_created_at:
                previous_created_at = parsed_created_at

        signature = event.get("signature")
        if signature is not None:
            signed_count += 1
            errors.append(f"event {event_id}: signature verification is not supported in MVP")

        if event.get("event_type") == "checkpoint":
            checkpoint_count += 1
            checkpointed = event.get("payload", {}).get("checkpointed_head_hash")
            if checkpointed != event.get("prev_event_hash"):
                errors.append(f"event {event_id}: checkpointed_head_hash must match prev_event_hash")

        previous_hash = event.get("event_hash")

    if signed_count == 0:
        signature_status = "unsigned"
    elif signed_count == len(events):
        signature_status = "invalid"
    else:
        signature_status = "invalid"

    anchor_status = "anchored" if checkpoint_count else "not_anchored"
    if checkpoint_count == 0:
        warnings.append("no checkpoint event found; tail truncation is not anchored")

    return VerificationReport(
        status=_status(errors, warnings),
        checked_events=len(events),
        head_hash=previous_hash,
        signature_status=signature_status,
        anchor_status=anchor_status,
        warnings=warnings,
        errors=errors,
    )


def _status(errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "fail"
    if warnings:
        return "warn"
    return "pass"
