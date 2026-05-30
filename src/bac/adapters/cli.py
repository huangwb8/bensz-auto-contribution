"""Command line adapter for BAC."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from secrets import token_hex
from typing import Any

from bac.core.anchor import (
    build_anchor_request,
    compute_anchor_hash,
    validate_anchor_receipt,
    verify_anchor_receipt,
)
from bac.core.canonicalize import canonical_json
from bac.core.schema import EVENT_TYPES, SOURCE_TYPES, TRUST_LEVELS
from bac.core.verify import verify_bac_file
from bac.report.inspect import timeline
from bac.service.event_builder import (
    build_anchor_checkpoint_event,
    build_genesis_event,
    build_record_event,
    default_actor,
)
from bac.storage.bac_file import DEFAULT_BAC_FILE, append_event, current_head_hash, initialize_bac_file, read_events


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bac", description="BAC contribution attribution ledger")
    parser.add_argument("--root", default=".", help="project root directory")
    parser.add_argument("--bac-file", default=DEFAULT_BAC_FILE, help="BAC v2 container file path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a BAC ledger with a genesis event")
    init.add_argument("--force", action="store_true", help="overwrite an existing BAC file")
    init.add_argument("--mode", choices=["local", "hybrid"], default="hybrid", help="BAC anchor mode")
    init.add_argument("--anchor-url", help="anchor service base URL for hybrid mode")
    init.add_argument("--actor-name", default="bac")
    init.add_argument("--actor-kind", default="system_tool")
    init.add_argument("--json", action="store_true", help="print machine-readable output")
    init.set_defaults(func=_cmd_init)

    record = subparsers.add_parser("record", help="append an event to a BAC ledger")
    record.add_argument("--event-type", required=True, choices=sorted(EVENT_TYPES - {"genesis"}))
    record.add_argument("--source-type", required=True, choices=sorted(SOURCE_TYPES))
    record.add_argument("--trust-level", choices=sorted(TRUST_LEVELS))
    record.add_argument("--summary", required=True)
    record.add_argument("--path", action="append", default=[], help="project file path to snapshot")
    record.add_argument("--command-text", help="command text to record")
    record.add_argument("--exit-code", type=int, help="command exit code")
    record.add_argument("--payload-json", help="additional payload object")
    record.add_argument("--evidence-json", help="additional evidence list")
    record.add_argument("--actor-name", default=None)
    record.add_argument("--actor-kind", default=None)
    record.add_argument("--session-id")
    record.add_argument("--json", action="store_true", help="print machine-readable output")
    record.set_defaults(func=_cmd_record)

    verify = subparsers.add_parser("verify", help="verify a BAC ledger")
    verify.add_argument("--require-anchor", action="store_true", help="fail unless a valid remote anchor receipt exists")
    verify.add_argument("--json", action="store_true", help="print machine-readable output")
    verify.set_defaults(func=_cmd_verify)

    inspect = subparsers.add_parser("inspect", help="show a contribution timeline")
    inspect.add_argument("--limit", type=int)
    inspect.add_argument("--json", action="store_true", help="print machine-readable output")
    inspect.set_defaults(func=_cmd_inspect)

    config = subparsers.add_parser("config", help="append BAC configuration metadata")
    config_subparsers = config.add_subparsers(dest="config_command", required=True)
    config_set = config_subparsers.add_parser("set", help="set a BAC configuration value")
    config_set.add_argument("key", choices=["mode", "anchor.url", "anchor.require"])
    config_set.add_argument("value")
    config_set.add_argument("--json", action="store_true", help="print machine-readable output")
    config_set.set_defaults(func=_cmd_config_set)
    config_get = config_subparsers.add_parser("get", help="print BAC configuration")
    config_get.add_argument("--json", action="store_true", help="print machine-readable output")
    config_get.set_defaults(func=_cmd_config_get)

    anchor = subparsers.add_parser("anchor", help="create and import private anchor receipts")
    anchor_subparsers = anchor.add_subparsers(dest="anchor_command", required=True)
    anchor_request = anchor_subparsers.add_parser("request", help="print a private anchor request")
    anchor_request.add_argument("--json", action="store_true", help="print machine-readable output")
    anchor_request.set_defaults(func=_cmd_anchor_request)
    anchor_import = anchor_subparsers.add_parser("import", help="append an anchored checkpoint from a receipt")
    anchor_import.add_argument("--receipt-file", required=True, help="JSON receipt returned by an anchor service")
    anchor_import.add_argument("--public-key", required=True, help="base64 raw Ed25519 public key")
    anchor_import.add_argument("--json", action="store_true", help="print machine-readable output")
    anchor_import.set_defaults(func=_cmd_anchor_import)
    anchor_push = anchor_subparsers.add_parser("push", help="submit the current head to the configured anchor service")
    anchor_push.add_argument("--public-key", help="base64 raw Ed25519 public key; fetched from service when omitted")
    anchor_push.add_argument("--json", action="store_true", help="print machine-readable output")
    anchor_push.set_defaults(func=_cmd_anchor_push)

    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    bac_path = _bac_path(root, args.bac_file)
    actor = default_actor(args.actor_name, args.actor_kind)
    bac_config = _default_config_for_init(args.mode, args.anchor_url)
    event = build_genesis_event(root, actor, bac_config)
    initialize_bac_file(bac_path, event, force=args.force)
    output = {
        "status": "initialized",
        "bac_file": str(bac_path),
        "head_hash": event["event_hash"],
        "mode": bac_config["mode"],
    }
    _print_output(output, args.json)
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    bac_path = _bac_path(root, args.bac_file)
    head_hash = current_head_hash(bac_path)
    if head_hash is None:
        raise FileNotFoundError(f"BAC file is empty or missing: {bac_path}")

    payload = _json_object(args.payload_json, "payload-json") if args.payload_json else {}
    evidence = _json_list(args.evidence_json, "evidence-json") if args.evidence_json else []
    actor = default_actor(
        args.actor_name or args.source_type,
        args.actor_kind or args.source_type,
        args.session_id,
    )
    event = build_record_event(
        root=root,
        prev_event_hash=head_hash,
        event_type=args.event_type,
        source_type=args.source_type,
        summary=args.summary,
        trust_level=args.trust_level,
        actor=actor,
        files=args.path,
        command=args.command_text,
        exit_code=args.exit_code,
        payload=payload,
        evidence=evidence,
    )
    append_event(bac_path, event)
    output = {
        "status": "recorded",
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "head_hash": event["event_hash"],
        "redactions": event["redactions"],
    }
    _print_output(output, args.json)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    report = verify_bac_file(_bac_path(root, args.bac_file), require_anchor=args.require_anchor)
    if args.json:
        print(canonical_json(report.to_dict()))
    else:
        print(f"status: {report.status}")
        print(f"checked_events: {report.checked_events}")
        print(f"head_hash: {report.head_hash}")
        print(f"signature_status: {report.signature_status}")
        print(f"anchor_status: {report.anchor_status}")
        for warning in report.warnings:
            print(f"warning: {warning}")
        for error in report.errors:
            print(f"error: {error}")
    return 0 if report.status in {"pass", "warn"} else 1


def _cmd_inspect(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    events = read_events(_bac_path(root, args.bac_file))
    items = timeline(events, args.limit)
    if args.json:
        print(canonical_json(items))
    else:
        for item in items:
            print(
                f"{item['created_at']}  {item['event_type']}  "
                f"{item['source_type']}/{item['trust_level']}  {item['summary']}"
            )
    return 0


def _cmd_config_set(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    bac_path = _bac_path(root, args.bac_file)
    events = read_events(bac_path)
    if not events:
        raise FileNotFoundError(f"BAC file is empty or missing: {bac_path}")
    config = _bac_config(events)
    if args.key == "mode":
        if args.value not in {"local", "hybrid"}:
            raise ValueError("mode must be local or hybrid")
        config["mode"] = args.value
    elif args.key == "anchor.require":
        config["anchor.require"] = _parse_bool(args.value)
    elif args.key == "anchor.url":
        config["anchor.url"] = args.value
    if not isinstance(config.get("anchor.ledger_nonce"), str):
        config["anchor.ledger_nonce"] = token_hex(32)
    event = build_record_event(
        root=root,
        prev_event_hash=events[-1]["event_hash"],
        event_type="verification",
        source_type="system",
        summary=f"Updated BAC config: {args.key}",
        trust_level="observed",
        actor=default_actor("bac", "system_tool"),
        payload={"bac_config": config},
    )
    append_event(bac_path, event)
    _print_output({"status": "configured", "key": args.key, "head_hash": event["event_hash"]}, args.json)
    return 0


def _cmd_config_get(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = _bac_config(read_events(_bac_path(root, args.bac_file)))
    _print_output(config, args.json)
    return 0


def _cmd_anchor_request(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    events = read_events(_bac_path(root, args.bac_file))
    if not events:
        raise FileNotFoundError("BAC file is empty or missing")
    request = _build_request_for_current_head(events)
    _print_output(request, args.json)
    return 0


def _cmd_anchor_import(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    bac_path = _bac_path(root, args.bac_file)
    events = read_events(bac_path)
    if not events:
        raise FileNotFoundError(f"BAC file is empty or missing: {bac_path}")
    receipt = _read_json_file(Path(args.receipt_file), "receipt-file")
    event = _append_anchor_checkpoint(root, bac_path, events, receipt, args.public_key)
    output = {"status": "anchored", "event_id": event["event_id"], "head_hash": event["event_hash"]}
    _print_output(output, args.json)
    return 0


def _cmd_anchor_push(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    bac_path = _bac_path(root, args.bac_file)
    events = read_events(bac_path)
    if not events:
        raise FileNotFoundError(f"BAC file is empty or missing: {bac_path}")
    config = _bac_config(events)
    anchor_url = config.get("anchor.url")
    if not isinstance(anchor_url, str) or not anchor_url:
        raise ValueError("anchor.url is not configured; run bac config set anchor.url <url>")

    request = _build_request_for_current_head(events)
    receipt = _post_anchor_request(anchor_url, request)
    public_key = args.public_key or _fetch_public_key(anchor_url, receipt.get("key_id"))
    event = _append_anchor_checkpoint(root, bac_path, events, receipt, public_key)
    output = {"status": "anchored", "event_id": event["event_id"], "head_hash": event["event_hash"]}
    _print_output(output, args.json)
    return 0


def _bac_path(root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return root / path


def _json_object(raw: str, label: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _json_list(raw: str, label: str) -> list[dict[str, Any]]:
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a JSON list of objects")
    return value


def _default_config_for_init(mode: str, anchor_url: str | None) -> dict[str, Any]:
    from bac.service.event_builder import default_bac_config

    config = default_bac_config()
    config["mode"] = mode
    if anchor_url:
        config["anchor.url"] = anchor_url
    return config


def _bac_config(events: list[dict[str, Any]]) -> dict[str, Any]:
    config: dict[str, Any] = {"mode": "hybrid", "anchor.require": False}
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        update = payload.get("bac_config")
        if isinstance(update, dict):
            config.update(update)
    return config


def _build_request_for_current_head(events: list[dict[str, Any]]) -> dict[str, Any]:
    config = _bac_config(events)
    ledger_nonce = config.get("anchor.ledger_nonce")
    if not isinstance(ledger_nonce, str) or not ledger_nonce:
        raise ValueError("anchor.ledger_nonce is missing; run bac config set mode hybrid")
    head_hash = events[-1].get("event_hash")
    if not isinstance(head_hash, str):
        raise ValueError("current BAC head is missing event_hash")
    sequence = sum(
        1
        for event in events
        if event.get("event_type") == "checkpoint"
        and isinstance(event.get("payload"), dict)
        and isinstance(event["payload"].get("anchor"), dict)
    ) + 1
    return build_anchor_request(
        head_hash=head_hash,
        ledger_nonce=ledger_nonce,
        sequence=sequence,
        ledger_id=config.get("anchor.ledger_id") if isinstance(config.get("anchor.ledger_id"), str) else None,
    )


def _append_anchor_checkpoint(
    root: Path,
    bac_path: Path,
    events: list[dict[str, Any]],
    receipt: dict[str, Any],
    public_key: str,
) -> dict[str, Any]:
    config = _bac_config(events)
    ledger_nonce = config.get("anchor.ledger_nonce")
    if not isinstance(ledger_nonce, str) or not ledger_nonce:
        raise ValueError("anchor.ledger_nonce is missing; run bac config set mode hybrid")
    head_hash = events[-1].get("event_hash")
    if not isinstance(head_hash, str):
        raise ValueError("current BAC head is missing event_hash")
    expected_anchor_hash = compute_anchor_hash(head_hash, ledger_nonce)
    if receipt.get("anchor_hash") != expected_anchor_hash:
        raise ValueError("receipt anchor_hash does not match the current BAC head")
    receipt_errors = validate_anchor_receipt(receipt)
    if receipt_errors:
        raise ValueError("; ".join(receipt_errors))
    verification = verify_anchor_receipt(receipt, public_key)
    if not verification.valid:
        raise ValueError("; ".join(verification.errors))
    event = build_anchor_checkpoint_event(
        root=root,
        prev_event_hash=head_hash,
        anchor_receipt=receipt,
        ledger_nonce=ledger_nonce,
        anchor_public_key=public_key,
    )
    append_event(bac_path, event)
    return event


def _read_json_file(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _post_anchor_request(anchor_url: str, request: dict[str, Any]) -> dict[str, Any]:
    url = anchor_url.rstrip("/") + "/api/v1/anchors"
    http_request = urllib.request.Request(
        url,
        data=canonical_json(request).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=15) as response:
            return _json_response(response.read(), "anchor service response")
    except urllib.error.URLError as exc:
        raise ValueError(f"anchor push failed: {exc}") from exc


def _fetch_public_key(anchor_url: str, key_id: Any) -> str:
    url = anchor_url.rstrip("/") + "/api/v1/public-keys"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = _json_response(response.read(), "public-keys response")
    except urllib.error.URLError as exc:
        raise ValueError(f"failed to fetch anchor public key: {exc}") from exc
    keys = payload.get("keys")
    if not isinstance(keys, list):
        raise ValueError("public-keys response is missing keys")
    for item in keys:
        if isinstance(item, dict) and item.get("key_id") == key_id and isinstance(item.get("public_key"), str):
            return item["public_key"]
    raise ValueError(f"anchor public key not found for key_id: {key_id}")


def _json_response(raw: bytes, label: str) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _parse_bool(raw: str) -> bool:
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("anchor.require must be true or false")


def _print_output(output: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(canonical_json(output))
    else:
        for key, value in output.items():
            print(f"{key}: {value}")
