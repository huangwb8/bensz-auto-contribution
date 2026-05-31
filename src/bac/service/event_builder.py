"""Build BAC events from adapter inputs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex
from typing import Any

from bac import __version__
from bac.core.hash_chain import attach_event_hash
from bac.core.schema import FORMAT_VERSION, EVENT_TYPES, SOURCE_TYPES, TRUST_LEVELS
from bac.service.evidence import collect_file_snapshots, collect_git_diff_evidence, collect_project_context
from bac.service.redaction import redact_data


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_event_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"bac_{stamp}_{uuid.uuid4().hex[:16]}"


def build_genesis_event(
    root: Path,
    actor: dict[str, Any] | None = None,
    bac_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "summary": "Initialized BAC ledger",
        "tool": "bac",
        "tool_version": __version__,
        "bac_config": bac_config or default_bac_config(),
    }
    return build_event(
        root=root,
        prev_event_hash=None,
        event_type="genesis",
        source_type="system",
        trust_level="observed",
        payload=payload,
        actor=actor or default_actor("bac", "system_tool"),
    )


def build_record_event(
    *,
    root: Path,
    prev_event_hash: str,
    event_type: str,
    source_type: str,
    summary: str,
    trust_level: str | None = None,
    actor: dict[str, Any] | None = None,
    files: list[str] | None = None,
    command: str | None = None,
    exit_code: int | None = None,
    payload: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    trust = trust_level or _default_trust(event_type, source_type)
    event_payload = dict(payload or {})
    event_payload["summary"] = summary

    event_evidence = list(evidence or [])
    if files:
        file_snapshots = collect_file_snapshots(root, files)
        event_payload["files"] = file_snapshots
        diff_evidence = collect_git_diff_evidence(root, files)
        if diff_evidence:
            event_evidence.append(diff_evidence)

    if command is not None:
        event_payload["command"] = command
        event_payload["exit_code"] = exit_code

    if event_type == "checkpoint":
        event_payload["checkpointed_head_hash"] = prev_event_hash

    return build_event(
        root=root,
        prev_event_hash=prev_event_hash,
        event_type=event_type,
        source_type=source_type,
        trust_level=trust,
        payload=event_payload,
        evidence=event_evidence,
        actor=actor or default_actor(source_type, source_type),
    )


def build_anchor_checkpoint_event(
    *,
    root: Path,
    prev_event_hash: str,
    anchor_receipt: dict[str, Any],
    ledger_nonce: str,
    anchor_public_key: str,
    summary: str = "Remote anchor checkpoint",
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_record_event(
        root=root,
        prev_event_hash=prev_event_hash,
        event_type="checkpoint",
        source_type="system",
        summary=summary,
        trust_level="anchored",
        actor=actor or default_actor("bac", "system_tool"),
        payload={
            "anchor": {
                "format": "bac.anchor.checkpoint.v1",
                "ledger_nonce": ledger_nonce,
                "anchor_public_key": anchor_public_key,
                "anchor_receipt": anchor_receipt,
            }
        },
    )


def build_event(
    *,
    root: Path,
    prev_event_hash: str | None,
    event_type: str,
    source_type: str,
    trust_level: str,
    payload: dict[str, Any],
    actor: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _validate_builder_input(event_type, source_type, trust_level)
    redacted_payload, payload_redactions = redact_data(payload)
    redacted_evidence, evidence_redactions = redact_data(evidence or [])

    event = {
        "format": FORMAT_VERSION,
        "event_id": new_event_id(),
        "event_type": event_type,
        "source_type": source_type,
        "trust_level": trust_level,
        "created_at": utc_now(),
        "project": collect_project_context(root),
        "actor": actor,
        "payload": redacted_payload,
        "evidence": redacted_evidence,
        "redactions": payload_redactions + evidence_redactions,
        "prev_event_hash": prev_event_hash,
        "event_hash": None,
        "signature": None,
    }
    return attach_event_hash(event)


def default_actor(name: str, kind: str, session_id: str | None = None) -> dict[str, Any]:
    actor = {
        "declared_name": name,
        "declared_kind": kind,
    }
    if session_id:
        actor["session_id"] = session_id
    return actor


def default_bac_config() -> dict[str, Any]:
    return {
        "mode": "hybrid",
        "anchor.require": False,
        "anchor.ledger_nonce": token_hex(32),
    }


def _default_trust(event_type: str, source_type: str) -> str:
    if event_type in {"tool_command", "file_snapshot", "file_change", "test_result", "checkpoint"}:
        return "observed" if event_type != "checkpoint" else "verified"
    if source_type in {"human", "ai"}:
        return "declared"
    return "observed"


def _validate_builder_input(event_type: str, source_type: str, trust_level: str) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type}")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unsupported source_type: {source_type}")
    if trust_level not in TRUST_LEVELS:
        raise ValueError(f"unsupported trust_level: {trust_level}")
    if trust_level == "signed":
        raise ValueError("signed trust_level is not supported until event signatures are implemented")
    if trust_level == "anchored" and event_type != "checkpoint":
        raise ValueError("anchored trust_level is only supported for anchor checkpoint events")
