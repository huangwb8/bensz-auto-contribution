"""BAC v2 container verification."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from bac.core import container
from bac.core.anchor import compute_anchor_hash, verify_anchor_receipt
from bac.core.container import (
    CONTAINER_FORMAT_VERSION,
    EVENT_PATH_TEMPLATE,
    MANIFEST_PATH,
    duplicate_names,
    event_sequence,
)
from bac.core.hash_chain import compute_event_hash, is_sha256
from bac.core.schema import FORMAT_VERSION, parse_created_at, validate_event_schema, validate_event_source_policy

HUMAN_INPUT_FORMAT = "bac.human_input.v1"
HUMAN_INPUT_EVIDENCE_TYPES = {"human_input_message", "prompt_log_block"}
HUMAN_INPUT_CLASSIFICATIONS = {"instruction", "review", "approval"}
AI_ACTIVITY_EVENT_TYPES = {"ai_plan", "ai_generation"}
FORBIDDEN_HUMAN_INPUT_TEXT_KEYS = {"full_text", "raw_text", "message", "prompt_text"}


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
    if path.stat().st_size > container.MAX_BAC_BYTES:
        return VerificationReport(
            status="fail",
            errors=[f"BAC file exceeds maximum size of {container.MAX_BAC_BYTES} bytes: {path}"],
        )

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
            if len(event_members) > container.MAX_EVENT_COUNT:
                errors.append(
                    f"container has too many event members: {len(event_members)} > {container.MAX_EVENT_COUNT}"
                )
                event_members = []
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
    previous_event_hashes: set[str] = set()
    has_ai_activity = False
    has_human_input_provenance = False

    if not events:
        return VerificationReport(status="fail", errors=["BAC file contains no events"])

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.extend(validate_event_schema(event, index + 1))
            continue

        event = dict(event)
        event_id = event.get("event_id", f"#{index + 1}")
        errors.extend(validate_event_schema(event, index + 1))
        errors.extend(
            validate_event_source_policy(
                event.get("event_type"),
                event.get("source_type"),
                prefix=f"event {event_id}: ",
            )
        )
        _validate_human_approval_reference(event, previous_event_hashes, errors)
        if _validate_human_input_provenance(event, errors):
            has_human_input_provenance = True
        if _is_ai_activity(event):
            has_ai_activity = True
        _append_semantic_warnings(event, warnings)

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
        if event.get("trust_level") == "signed":
            signed_count += 1
            errors.append(f"event {event_id}: signed trust_level requires a valid signature")
        elif signature is not None:
            signed_count += 1
            errors.append(f"event {event_id}: signature verification is not supported yet")

        anchor_valid = False
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
                    anchor_valid = True
                    valid_receipt_count += 1
                else:
                    invalid_receipt_count += 1
            else:
                local_checkpoint_count += 1
        elif event.get("trust_level") == "anchored":
            errors.append(f"event {event_id}: anchored trust_level is only valid on checkpoint events")

        if event.get("trust_level") == "anchored" and not anchor_valid:
            errors.append(f"event {event_id}: anchored trust_level requires a valid remote anchor receipt")

        previous_hash = event.get("event_hash")
        if is_sha256(previous_hash):
            previous_event_hashes.add(previous_hash)

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
    if has_ai_activity and not has_human_input_provenance:
        warnings.append("ledger has AI activity but no human input provenance; human contributions may be underrecorded")
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


def _validate_human_approval_reference(
    event: dict[str, Any],
    previous_event_hashes: set[str],
    errors: list[str],
) -> None:
    if event.get("event_type") != "human_approval":
        return
    payload = event.get("payload")
    if not isinstance(payload, dict) or "approves_event_hash" not in payload:
        return

    event_id = event.get("event_id", "<unknown>")
    approved_hash = payload.get("approves_event_hash")
    if not is_sha256(approved_hash):
        errors.append(f"event {event_id}: payload.approves_event_hash must be sha256:<hex>")
        return
    if approved_hash == event.get("event_hash"):
        errors.append(f"event {event_id}: payload.approves_event_hash cannot reference itself")
        return
    if approved_hash not in previous_event_hashes:
        errors.append(f"event {event_id}: payload.approves_event_hash must reference a previous event_hash")


def _validate_human_input_provenance(event: dict[str, Any], errors: list[str]) -> bool:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    provenance = payload.get("input_provenance")
    if provenance is None:
        return False

    event_id = event.get("event_id", "<unknown>")
    if not isinstance(provenance, dict):
        errors.append(f"event {event_id}: payload.input_provenance must be an object")
        return False

    _append_forbidden_human_input_text_errors(event_id, payload, "payload", errors)
    evidence = event.get("evidence")
    if isinstance(evidence, list):
        _append_forbidden_human_input_text_errors(event_id, evidence, "evidence", errors)

    if provenance.get("format") != HUMAN_INPUT_FORMAT:
        errors.append(f"event {event_id}: input_provenance.format must be {HUMAN_INPUT_FORMAT}")
    channel = provenance.get("channel")
    if not isinstance(channel, str) or not channel:
        errors.append(f"event {event_id}: input_provenance.channel must be a non-empty string")
    message_hash = provenance.get("message_hash")
    if not is_sha256(message_hash):
        errors.append(f"event {event_id}: input_provenance.message_hash must be sha256:<hex>")
    recorded_full_text = provenance.get("recorded_full_text")
    if not isinstance(recorded_full_text, bool):
        errors.append(f"event {event_id}: input_provenance.recorded_full_text must be a boolean")
    elif recorded_full_text:
        errors.append(f"event {event_id}: input_provenance.recorded_full_text must be false by default")
    classification = provenance.get("classification")
    if not isinstance(classification, str) or classification not in HUMAN_INPUT_CLASSIFICATIONS:
        errors.append(f"event {event_id}: input_provenance.classification must be one of {sorted(HUMAN_INPUT_CLASSIFICATIONS)}")

    if "source_path" in provenance:
        source_path = provenance.get("source_path")
        if not isinstance(source_path, str) or not source_path or Path(source_path).is_absolute() or ".." in Path(source_path).parts:
            errors.append(f"event {event_id}: input_provenance.source_path must be a relative project path")
    _validate_optional_positive_int(event_id, provenance, "message_index", errors)
    _validate_line_range(event_id, provenance, errors)

    if not isinstance(evidence, list):
        return True
    matching_evidence = [
        item
        for item in evidence
        if isinstance(item, dict) and item.get("type") in HUMAN_INPUT_EVIDENCE_TYPES
    ]
    if not matching_evidence:
        errors.append(f"event {event_id}: human input provenance requires human input evidence")
        return True
    for item in matching_evidence:
        if item.get("message_hash") != message_hash:
            errors.append(f"event {event_id}: human input evidence message_hash must match input_provenance.message_hash")
        if item.get("redacted") is not True:
            errors.append(f"event {event_id}: human input evidence must be marked redacted")
        if "source_path" in item:
            source_path = item.get("source_path")
            if not isinstance(source_path, str) or not source_path or Path(source_path).is_absolute() or ".." in Path(source_path).parts:
                errors.append(f"event {event_id}: human input evidence source_path must be a relative project path")
        _validate_line_range(event_id, item, errors, label="human input evidence")
    return True


def _validate_optional_positive_int(
    event_id: str,
    value: dict[str, Any],
    key: str,
    errors: list[str],
) -> None:
    if key in value and (not isinstance(value.get(key), int) or value.get(key) < 1):
        errors.append(f"event {event_id}: input_provenance.{key} must be a positive integer")


def _validate_line_range(
    event_id: str,
    value: dict[str, Any],
    errors: list[str],
    label: str = "input_provenance",
) -> None:
    start = value.get("start_line")
    end = value.get("end_line")
    if start is None and end is None:
        return
    if not isinstance(start, int) or start < 1 or not isinstance(end, int) or end < start:
        errors.append(f"event {event_id}: {label}.start_line/end_line must be positive and ordered")


def _append_forbidden_human_input_text_errors(
    event_id: str,
    value: Any,
    path: str,
    errors: list[str],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if key in FORBIDDEN_HUMAN_INPUT_TEXT_KEYS:
                errors.append(f"event {event_id}: {item_path} must not store full human input text")
            _append_forbidden_human_input_text_errors(event_id, item, item_path, errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _append_forbidden_human_input_text_errors(event_id, item, f"{path}[{index}]", errors)


def _is_ai_activity(event: dict[str, Any]) -> bool:
    event_type = event.get("event_type")
    if isinstance(event_type, str) and event_type in AI_ACTIVITY_EVENT_TYPES:
        return True
    return event_type == "file_change" and event.get("source_type") == "ai"


def _append_semantic_warnings(event: dict[str, Any], warnings: list[str]) -> None:
    event_id = event.get("event_id", "<unknown>")
    actor = event.get("actor")
    actor_kind = actor.get("declared_kind") if isinstance(actor, dict) else None
    source_type = event.get("source_type")
    if actor_kind in {"human", "ai", "tool", "system"} and actor_kind != source_type:
        warnings.append(f"event {event_id}: actor.declared_kind {actor_kind} conflicts with source_type {source_type}")

    if (
        event.get("event_type") == "file_change"
        and source_type == "human"
        and _payload_mentions_ai_or_tool_generation(event.get("payload"))
    ):
        warnings.append(
            f"event {event_id}: file_change/source_type=human appears to describe AI or tool generated content; "
            "record AI work as ai_generation/source_type=ai, then append human_approval/source_type=human"
        )


def _payload_mentions_ai_or_tool_generation(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    text = " ".join(_payload_strings(payload)).lower()
    ai_markers = ("ai-generated", "ai generated", "ai 生成", "ai生成", "人工智能生成")
    tool_markers = ("tool-generated", "tool generated", "generated by tool", "工具生成", "命令生成")
    return any(marker in text for marker in ai_markers + tool_markers)


def _payload_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_payload_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_payload_strings(item))
        return strings
    return []


def _status(errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "fail"
    if warnings:
        return "warn"
    return "pass"


def _read_json_member(archive: ZipFile, name: str, errors: list[str]) -> Any:
    try:
        info = archive.getinfo(name)
    except KeyError:
        errors.append(f"{name}: container member is missing")
        return None
    if info.file_size > container.MAX_MEMBER_UNCOMPRESSED_BYTES:
        errors.append(
            f"{name}: uncompressed size exceeds limit of {container.MAX_MEMBER_UNCOMPRESSED_BYTES} bytes"
        )
        return None
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
