"""BAC v2 container verification."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from bac.core.anchor import compute_anchor_hash, verify_anchor_receipt
from bac.core.container import (
    CONTAINER_FORMAT_VERSION,
    EVENT_PATH_TEMPLATE,
    MANIFEST_PATH,
    duplicate_names,
    event_sequence,
)
from bac.core.hash_chain import compute_event_hash
from bac.core.schema import FORMAT_VERSION, parse_created_at, validate_event_schema


@dataclass
class VerificationReport:
    status: str
    checked_events: int = 0
    head_hash: str | None = None
    signature_status: str = "unsigned"
    anchor_status: str = "not_anchored"
    anchored_head_hashes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checked_events": self.checked_events,
            "head_hash": self.head_hash,
            "signature_status": self.signature_status,
            "anchor_status": self.anchor_status,
            "anchored_head_hashes": self.anchored_head_hashes,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def verify_bac_file(path: Path, require_anchor: bool = False) -> VerificationReport:
    if not path.exists():
        return VerificationReport(status="fail", errors=[f"BAC file does not exist: {path}"])

    events: list[Any] = []
    errors: list[str] = []
    manifest: dict[str, Any] | None = None
    try:
        with ZipFile(path, "r") as archive:
            names = archive.namelist()
            for duplicate in duplicate_names(names):
                errors.append(f"container has duplicate entry: {duplicate}")

            if MANIFEST_PATH not in names:
                errors.append(f"container missing {MANIFEST_PATH}")
            else:
                manifest = _read_json_member(archive, MANIFEST_PATH, errors)
                errors.extend(_validate_manifest(manifest))

            event_members = sorted(
                (sequence, name)
                for name in names
                if (sequence := event_sequence(name)) is not None
            )
            errors.extend(_validate_event_sequences([sequence for sequence, _name in event_members]))
            for _sequence, name in event_members:
                events.append(_read_json_member(archive, name, errors))
    except BadZipFile:
        return VerificationReport(
            status="fail",
            errors=[f"BAC file is not a valid v2 ZIP container: {path}"],
        )

    report = verify_events(events, require_anchor=require_anchor)
    report.errors = errors + _manifest_consistency_errors(manifest, events) + report.errors
    report.status = _status(report.errors, report.warnings)
    return report


def verify_events(events: list[Any], require_anchor: bool = False) -> VerificationReport:
    warnings: list[str] = []
    errors: list[str] = []
    previous_hash: str | None = None
    previous_created_at = None
    project_root_hash: str | None = None
    signed_count = 0
    checkpoint_count = 0
    local_checkpoint_count = 0
    valid_receipt_count = 0
    invalid_receipt_count = 0
    anchored_head_hashes: list[str] = []

    if not events:
        return VerificationReport(status="fail", errors=["BAC file contains no events"])

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.extend(validate_event_schema(event, index + 1))
            continue

        event = dict(event)
        errors.extend(validate_event_schema(event))

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
            errors.append(f"event {event_id}: signature verification is not supported yet")

        if event.get("event_type") == "checkpoint":
            checkpoint_count += 1
            payload = event.get("payload", {})
            checkpointed = payload.get("checkpointed_head_hash") if isinstance(payload, dict) else None
            if checkpointed != event.get("prev_event_hash"):
                errors.append(f"event {event_id}: checkpointed_head_hash must match prev_event_hash")
            anchor = payload.get("anchor") if isinstance(payload, dict) else None
            if isinstance(anchor, dict) and anchor.get("anchor_receipt") is not None:
                if _verify_checkpoint_anchor(
                    event_id,
                    checkpointed,
                    anchor,
                    anchored_head_hashes,
                    errors,
                ):
                    valid_receipt_count += 1
                else:
                    invalid_receipt_count += 1
            else:
                local_checkpoint_count += 1

        previous_hash = event.get("event_hash")

    if signed_count == 0:
        signature_status = "unsigned"
    elif signed_count == len(events):
        signature_status = "invalid"
    else:
        signature_status = "invalid"

    if invalid_receipt_count:
        anchor_status = "receipt_invalid"
    elif valid_receipt_count:
        anchor_status = "receipt_valid"
    elif local_checkpoint_count:
        anchor_status = "local_checkpoint"
    else:
        anchor_status = "not_anchored"

    if checkpoint_count == 0:
        warnings.append("no checkpoint event found; tail truncation is not anchored")
    if require_anchor and anchor_status != "receipt_valid":
        errors.append("a valid remote anchor receipt is required but was not found")

    return VerificationReport(
        status=_status(errors, warnings),
        checked_events=len(events),
        head_hash=previous_hash,
        signature_status=signature_status,
        anchor_status=anchor_status,
        anchored_head_hashes=anchored_head_hashes,
        warnings=warnings,
        errors=errors,
    )


def _verify_checkpoint_anchor(
    event_id: str,
    checkpointed_head_hash: Any,
    anchor: dict[str, Any],
    anchored_head_hashes: list[str],
    errors: list[str],
) -> bool:
    receipt = anchor.get("anchor_receipt")
    public_key = anchor.get("anchor_public_key")
    ledger_nonce = anchor.get("ledger_nonce")
    if not isinstance(checkpointed_head_hash, str):
        errors.append(f"event {event_id}: anchored checkpoint is missing checkpointed_head_hash")
        return False
    if not isinstance(receipt, dict):
        errors.append(f"event {event_id}: anchor_receipt must be an object")
        return False
    if not isinstance(public_key, str) or not public_key:
        errors.append(f"event {event_id}: anchor_public_key is required for receipt verification")
        return False
    try:
        expected_anchor_hash = compute_anchor_hash(checkpointed_head_hash, ledger_nonce)
    except ValueError as exc:
        errors.append(f"event {event_id}: {exc}")
        return False
    if receipt.get("anchor_hash") != expected_anchor_hash:
        errors.append(f"event {event_id}: anchor_receipt.anchor_hash does not match checkpointed head")
        return False

    result = verify_anchor_receipt(receipt, public_key)
    if not result.valid:
        errors.extend(f"event {event_id}: {error}" for error in result.errors)
        return False
    anchored_head_hashes.append(checkpointed_head_hash)
    return True


def _status(errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "fail"
    if warnings:
        return "warn"
    return "pass"


def _read_json_member(archive: ZipFile, name: str, errors: list[str]) -> Any:
    try:
        return json.loads(archive.read(name).decode("utf-8"))
    except UnicodeDecodeError:
        errors.append(f"{name}: content must be UTF-8 JSON")
    except json.JSONDecodeError as exc:
        errors.append(f"{name}: invalid JSON: {exc.msg}")
    return None


def _validate_manifest(manifest: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return [f"{MANIFEST_PATH}: manifest must be a JSON object"]
    if manifest.get("format") != CONTAINER_FORMAT_VERSION:
        errors.append(f"{MANIFEST_PATH}: format must be {CONTAINER_FORMAT_VERSION}")
    if manifest.get("event_format") != FORMAT_VERSION:
        errors.append(f"{MANIFEST_PATH}: event_format must be {FORMAT_VERSION}")
    project = manifest.get("project")
    if not isinstance(project, dict):
        errors.append(f"{MANIFEST_PATH}: project must be an object")
    genesis_hash = manifest.get("genesis_event_hash")
    if not isinstance(genesis_hash, str):
        errors.append(f"{MANIFEST_PATH}: genesis_event_hash must be a string")
    storage = manifest.get("storage")
    if not isinstance(storage, dict) or storage.get("kind") != "zip":
        errors.append(f"{MANIFEST_PATH}: storage.kind must be zip")
    elif storage.get("event_path_template") != EVENT_PATH_TEMPLATE:
        errors.append(f"{MANIFEST_PATH}: storage.event_path_template must be {EVENT_PATH_TEMPLATE}")
    return errors


def _validate_event_sequences(sequences: list[int]) -> list[str]:
    if not sequences:
        return ["container contains no event entries"]
    expected = list(range(1, len(sequences) + 1))
    if sequences != expected:
        return [f"event entries must be contiguous starting at 1; found {sequences}"]
    return []


def _manifest_consistency_errors(manifest: dict[str, Any] | None, events: list[Any]) -> list[str]:
    if not isinstance(manifest, dict) or not events or not isinstance(events[0], dict):
        return []

    errors: list[str] = []
    first_event = events[0]
    if manifest.get("genesis_event_hash") != first_event.get("event_hash"):
        errors.append(f"{MANIFEST_PATH}: genesis_event_hash does not match first event")
    if manifest.get("project", {}).get("root_hash") != first_event.get("project", {}).get("root_hash"):
        errors.append(f"{MANIFEST_PATH}: project.root_hash does not match first event")
    return errors
