"""Repair narrow, mechanically stale BAC ledger tails."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from bac.core.hash_chain import compute_event_hash, is_sha256
from bac.core.verify import verify_bac_file, verify_events
from bac.service.event_builder import build_record_event, default_actor
from bac.storage.bac_file import append_event, read_events, rewrite_events_atomic


REPAIR_TYPE = "stale-tail"
REPAIR_EVENT_FORMAT = "bac.repair.stale_tail.v1"
ALLOWED_REPAIR_FIELDS = {"prev_event_hash", "event_hash"}


def repair_stale_tail(
    root: Path,
    bac_path: Path,
    *,
    apply: bool = False,
    max_events: int = 8,
) -> dict[str, Any]:
    """Plan or apply a stale-tail repair.

    The repair is intentionally narrow: it may only rewrite tail event
    ``prev_event_hash`` values and their derived ``event_hash`` values. Any
    attribution, payload, evidence, signature, or checkpoint payload change is
    refused.
    """

    base_output = {
        "repair_type": REPAIR_TYPE,
        "apply": apply,
        "bac_file": str(bac_path),
    }
    try:
        if max_events < 1:
            raise ValueError("--max-events must be at least 1")
        events = read_events(bac_path)
    except ValueError as exc:
        return _refused(base_output, str(exc))

    report = verify_bac_file(bac_path)
    if not events:
        return _refused(base_output, "BAC file contains no events", errors=report.errors)
    if not report.errors:
        output = dict(base_output)
        output.update(
            {
                "status": "noop",
                "affected_events": [],
                "head_hash": events[-1].get("event_hash"),
                "warnings": report.warnings,
            }
        )
        return output

    plan = _plan_stale_tail(events, max_events=max_events)
    if plan.get("status") == "refused":
        output = dict(base_output)
        output.update(plan)
        output.setdefault("errors", report.errors)
        return output

    if not apply:
        output = dict(base_output)
        output.update(_public_plan(plan))
        output["status"] = "planned"
        output["warnings"] = report.warnings
        return output

    repaired_events = plan.pop("_repaired_events")
    _atomic_rewrite_events(bac_path, repaired_events)
    repaired_report = verify_bac_file(bac_path)
    if repaired_report.errors:
        return _refused(
            base_output,
            "repaired ledger did not pass verification before repair record append",
            errors=repaired_report.errors,
        )

    repair_event = _append_repair_record(root, bac_path, plan["affected_events"])
    checkpoint = _append_local_checkpoint(root, bac_path, repair_event)
    final_report = verify_bac_file(bac_path)
    if final_report.errors:
        return _refused(base_output, "repaired ledger did not pass final verification", errors=final_report.errors)

    output = dict(base_output)
    output.update(_public_plan(plan))
    output.update(
        {
            "status": "repaired",
            "repair_event_id": repair_event["event_id"],
            "checkpoint_event_id": checkpoint["event_id"],
            "head_hash": checkpoint["event_hash"],
            "warnings": final_report.warnings,
        }
    )
    return output


def _plan_stale_tail(events: list[dict[str, Any]], *, max_events: int) -> dict[str, Any]:
    mismatch = _first_hash_mismatch(events)
    if mismatch is not None:
        return _refused({}, f"event {mismatch} has event_hash mismatch unrelated to stale-tail repair")

    break_index = _first_prev_mismatch(events)
    if break_index is None:
        return _refused({}, "ledger verification failed, but no stale-tail prev_event_hash mismatch was found")
    if break_index == 0:
        return _refused({}, "genesis prev_event_hash mismatch is not a stale-tail repair")

    affected_count = len(events) - break_index
    if affected_count > max_events:
        return _refused({}, f"stale-tail repair would affect {affected_count} events, exceeding max-events {max_events}")

    stale_base = events[break_index].get("prev_event_hash")
    previous_hashes = {event.get("event_hash") for event in events[:break_index]}
    if not is_sha256(stale_base) or stale_base not in previous_hashes:
        return _refused({}, f"event {break_index + 1} prev_event_hash is not a previous ledger hash")

    repaired_events = deepcopy(events)
    affected: list[dict[str, Any]] = []
    previous_new_hash = repaired_events[break_index - 1].get("event_hash")
    previous_old_hash = events[break_index - 1].get("event_hash")
    if not is_sha256(previous_new_hash) or not is_sha256(previous_old_hash):
        return _refused({}, "event before stale tail is missing a valid event_hash")

    for index in range(break_index, len(events)):
        original = events[index]
        if _is_protected_tail_event(original):
            return _refused({}, f"event {index + 1} is signed, anchored, checkpointed, or signature-bound")

        old_prev = original.get("prev_event_hash")
        if index == break_index:
            allowed_old_prev = {stale_base}
        else:
            allowed_old_prev = {stale_base, previous_old_hash}
        if old_prev not in allowed_old_prev:
            return _refused({}, f"event {index + 1} is not part of a unique contiguous stale tail")

        repaired = repaired_events[index]
        old_hash = original.get("event_hash")
        repaired["prev_event_hash"] = previous_new_hash
        repaired["event_hash"] = compute_event_hash(repaired)
        if not _only_allowed_fields_changed(original, repaired):
            return _refused({}, f"event {index + 1} repair requires changing fields beyond prev_event_hash and event_hash")

        affected.append(
            {
                "sequence": index + 1,
                "event_id": original.get("event_id"),
                "old_prev_event_hash": old_prev,
                "new_prev_event_hash": repaired["prev_event_hash"],
                "old_event_hash": old_hash,
                "new_event_hash": repaired["event_hash"],
            }
        )
        previous_old_hash = old_hash
        previous_new_hash = repaired["event_hash"]

    repaired_report = verify_events(repaired_events)
    if repaired_report.errors:
        return _refused({}, "planned repair does not produce a valid ledger", errors=repaired_report.errors)

    return {
        "status": "planned",
        "affected_events": affected,
        "head_hash": repaired_events[-1].get("event_hash"),
        "_repaired_events": repaired_events,
    }


def _first_hash_mismatch(events: list[dict[str, Any]]) -> int | None:
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            return index
        if event.get("event_hash") != compute_event_hash(event):
            return index
    return None


def _first_prev_mismatch(events: list[dict[str, Any]]) -> int | None:
    previous_hash: str | None = None
    for index, event in enumerate(events):
        if index == 0:
            if event.get("prev_event_hash") is not None:
                return index
        elif event.get("prev_event_hash") != previous_hash:
            return index
        previous_hash = event.get("event_hash")
    return None


def _is_protected_tail_event(event: dict[str, Any]) -> bool:
    if event.get("trust_level") in {"signed", "anchored"}:
        return True
    if event.get("signature") is not None:
        return True
    if event.get("event_type") == "checkpoint":
        return True
    payload = event.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("anchor"), dict):
        return True
    return False


def _only_allowed_fields_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    keys = set(before) | set(after)
    for key in keys - ALLOWED_REPAIR_FIELDS:
        if before.get(key) != after.get(key):
            return False
    return True


def _atomic_rewrite_events(path: Path, events: list[dict[str, Any]]) -> None:
    rewrite_events_atomic(path, events)


def _append_repair_record(root: Path, bac_path: Path, affected_events: list[dict[str, Any]]) -> dict[str, Any]:
    events = read_events(bac_path)
    summary = "Repaired stale BAC tail without changing attribution fields"
    event = build_record_event(
        root=root,
        prev_event_hash=events[-1]["event_hash"],
        event_type="tool_command",
        source_type="tool",
        summary=summary,
        trust_level="observed",
        actor=default_actor("bac", "system_tool"),
        command="bac repair stale-tail --apply",
        exit_code=0,
        payload={
            "repair": {
                "format": REPAIR_EVENT_FORMAT,
                "repair_type": REPAIR_TYPE,
                "allowed_changed_fields": sorted(ALLOWED_REPAIR_FIELDS),
                "affected_events": affected_events,
            }
        },
    )
    append_event(bac_path, event)
    return event


def _append_local_checkpoint(root: Path, bac_path: Path, repair_event: dict[str, Any]) -> dict[str, Any]:
    event = build_record_event(
        root=root,
        prev_event_hash=repair_event["event_hash"],
        event_type="checkpoint",
        source_type="system",
        summary="Local checkpoint after stale-tail repair",
        trust_level="verified",
        actor=default_actor("bac", "system_tool"),
    )
    append_event(bac_path, event)
    return event


def _refused(base: dict[str, Any], reason: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    output = dict(base)
    output.update(
        {
            "status": "refused",
            "reason": reason,
            "refused": True,
            "errors": errors or [reason],
        }
    )
    return output


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if not key.startswith("_")}
