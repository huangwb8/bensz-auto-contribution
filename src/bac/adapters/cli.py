"""Command line adapter for BAC."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse
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
from bac.core.hash_chain import is_sha256
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
    inspect.add_argument("--human", action="store_true", help="shortcut for --source-type human")
    inspect.add_argument("--source-type", choices=sorted(SOURCE_TYPES), help="filter events by source type")
    inspect.add_argument("--since", help="include events at or after a UTC date or ISO-8601 timestamp")
    inspect.add_argument("--until", help="include events through a UTC date or before an ISO-8601 timestamp")
    inspect.add_argument("--on", help="include events on a single UTC date in YYYY-MM-DD format")
    inspect.add_argument("--json", action="store_true", help="print machine-readable output")
    inspect.set_defaults(func=_cmd_inspect)

    config = subparsers.add_parser("config", help="append BAC configuration metadata")
    config_subparsers = config.add_subparsers(dest="config_command", required=True)
    config_set = config_subparsers.add_parser("set", help="set a BAC configuration value")
    config_set.add_argument(
        "key",
        choices=["mode", "anchor.url", "anchor.require", "anchor.ledger_id", "cloud.auto_anchor"],
    )
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
    anchor_push.add_argument(
        "--token",
        help="bearer token for production anchor writes; defaults to BAC_ANCHOR_API_TOKEN",
    )
    anchor_push.add_argument(
        "--allow-insecure-anchor-url",
        action="store_true",
        help="allow http/private anchor URLs for local development",
    )
    anchor_push.add_argument("--json", action="store_true", help="print machine-readable output")
    anchor_push.set_defaults(func=_cmd_anchor_push)

    cloud = subparsers.add_parser("cloud", help="connect this BAC ledger to a BAC cloud service")
    cloud_subparsers = cloud.add_subparsers(dest="cloud_command", required=True)

    cloud_register = cloud_subparsers.add_parser("register", help="register a BAC cloud account and store a local token")
    _add_cloud_auth_args(cloud_register)
    cloud_register.set_defaults(func=_cmd_cloud_register)

    cloud_login = cloud_subparsers.add_parser("login", help="log in to BAC cloud and store a local token")
    _add_cloud_auth_args(cloud_login)
    cloud_login.set_defaults(func=_cmd_cloud_login)

    cloud_link = cloud_subparsers.add_parser("link", help="bind the current .bac ledger to a cloud ledger")
    cloud_link.add_argument("--url", help="BAC cloud base URL; defaults to existing anchor.url")
    cloud_link.add_argument("--token", help="bearer token; defaults to stored credentials or BAC_ANCHOR_API_TOKEN")
    cloud_link.add_argument("--ledger-name", help="display name in BAC cloud")
    cloud_link.add_argument(
        "--allow-insecure-anchor-url",
        action="store_true",
        help="allow http/private cloud URLs for local development",
    )
    cloud_link.add_argument("--json", action="store_true", help="print machine-readable output")
    cloud_link.set_defaults(func=_cmd_cloud_link)

    cloud_status = cloud_subparsers.add_parser("status", help="show cloud binding and token status")
    cloud_status.add_argument("--url", help="BAC cloud base URL; defaults to existing anchor.url")
    cloud_status.add_argument("--token", help="bearer token; defaults to stored credentials or BAC_ANCHOR_API_TOKEN")
    cloud_status.add_argument("--json", action="store_true", help="print machine-readable output")
    cloud_status.set_defaults(func=_cmd_cloud_status)

    return parser


def _add_cloud_auth_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", required=True, help="BAC cloud base URL")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", help="password; prompts when omitted")
    parser.add_argument(
        "--allow-insecure-anchor-url",
        action="store_true",
        help="allow http/private cloud URLs for local development",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable output")


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
    events = read_events(bac_path)
    if not events:
        raise FileNotFoundError(f"BAC file is empty or missing: {bac_path}")
    head_hash = events[-1].get("event_hash")
    if not isinstance(head_hash, str):
        raise ValueError("current BAC head is missing event_hash")
    config = _bac_config(events)
    if args.trust_level in {"signed", "anchored"}:
        raise ValueError(
            f"{args.trust_level} trust_level is reserved; use event signatures or bac anchor import/push"
        )

    payload = _json_object(args.payload_json, "payload-json") if args.payload_json else {}
    evidence = _json_list(args.evidence_json, "evidence-json") if args.evidence_json else []
    _validate_record_payload_against_ledger(args.event_type, payload, events)
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
    if config.get("cloud.auto_anchor"):
        checkpoint = _anchor_push_current(root, bac_path)
        output["cloud_anchor"] = {
            "status": "anchored",
            "event_id": checkpoint["event_id"],
            "head_hash": checkpoint["event_hash"],
        }
    _print_output(output, args.json)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    bac_path = _bac_path(root, args.bac_file)
    try:
        events = read_events(bac_path)
    except ValueError:
        events = []
    config = _bac_config(events)
    effective_require_anchor = args.require_anchor or bool(config.get("anchor.require"))
    report = verify_bac_file(bac_path, require_anchor=effective_require_anchor)
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
    source_type = args.source_type
    if args.human:
        if source_type and source_type != "human":
            raise ValueError("--human cannot be combined with --source-type other than human")
        source_type = "human"
    events = read_events(_bac_path(root, args.bac_file))
    items = timeline(
        events,
        limit=args.limit,
        source_type=source_type,
        since=args.since,
        until=args.until,
        on=args.on,
    )
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
    elif args.key == "anchor.ledger_id":
        config["anchor.ledger_id"] = args.value
    elif args.key == "cloud.auto_anchor":
        config["cloud.auto_anchor"] = _parse_bool(args.value)
    if not isinstance(config.get("anchor.ledger_nonce"), str):
        config["anchor.ledger_nonce"] = token_hex(32)
    event = _append_config_event(root, bac_path, events, config, f"Updated BAC config: {args.key}")
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
    event = _anchor_push_current(
        root,
        bac_path,
        public_key=args.public_key,
        token=args.token,
        allow_insecure=args.allow_insecure_anchor_url,
    )
    output = {"status": "anchored", "event_id": event["event_id"], "head_hash": event["event_hash"]}
    _print_output(output, args.json)
    return 0


def _cmd_cloud_register(args: argparse.Namespace) -> int:
    _validate_anchor_push_url(args.url, allow_insecure=args.allow_insecure_anchor_url)
    password = args.password or getpass.getpass("BAC cloud password: ")
    response = _post_cloud_json(args.url, "/api/v1/auth/register", {"email": args.email, "password": password})
    _store_cloud_token(args.url, response["token"], response.get("user", {}).get("email"))
    _print_output(
        {
            "status": "registered",
            "url": args.url.rstrip("/"),
            "email": response.get("user", {}).get("email"),
            "token": response["token"],
        },
        args.json,
    )
    return 0


def _cmd_cloud_login(args: argparse.Namespace) -> int:
    _validate_anchor_push_url(args.url, allow_insecure=args.allow_insecure_anchor_url)
    password = args.password or getpass.getpass("BAC cloud password: ")
    response = _post_cloud_json(args.url, "/api/v1/auth/login", {"email": args.email, "password": password})
    _store_cloud_token(args.url, response["token"], response.get("user", {}).get("email"))
    _print_output(
        {
            "status": "logged_in",
            "url": args.url.rstrip("/"),
            "email": response.get("user", {}).get("email"),
            "token": response["token"],
        },
        args.json,
    )
    return 0


def _cmd_cloud_link(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    bac_path = _bac_path(root, args.bac_file)
    events = read_events(bac_path)
    if not events:
        raise FileNotFoundError(f"BAC file is empty or missing: {bac_path}")
    config = _bac_config(events)
    anchor_url = args.url or config.get("anchor.url")
    if not isinstance(anchor_url, str) or not anchor_url:
        raise ValueError("cloud URL is missing; pass --url or configure anchor.url")
    _validate_anchor_push_url(anchor_url, allow_insecure=args.allow_insecure_anchor_url)
    token = args.token or os.getenv("BAC_ANCHOR_API_TOKEN") or _cloud_token_for(anchor_url)
    if not token:
        raise ValueError("BAC cloud token is missing; run bac cloud login/register or pass --token")
    ledger_name = args.ledger_name or root.name or "BAC ledger"
    response = _post_cloud_json(
        anchor_url,
        "/api/v1/cloud/ledgers",
        {"display_name": ledger_name},
        token=token,
    )
    config.update(
        {
            "mode": "hybrid",
            "anchor.url": anchor_url.rstrip("/"),
            "anchor.require": True,
            "anchor.ledger_id": response["ledger_id"],
            "cloud.auto_anchor": True,
            "anchor.allow_insecure": bool(args.allow_insecure_anchor_url),
        }
    )
    if not isinstance(config.get("anchor.ledger_nonce"), str):
        config["anchor.ledger_nonce"] = token_hex(32)
    event = _append_config_event(root, bac_path, events, config, "Linked BAC ledger to cloud")
    checkpoint = _anchor_push_current(
        root,
        bac_path,
        token=token,
        allow_insecure=args.allow_insecure_anchor_url,
    )
    output = {
        "status": "linked",
        "url": anchor_url.rstrip("/"),
        "ledger_id": response["ledger_id"],
        "config_event_id": event["event_id"],
        "anchored_event_id": checkpoint["event_id"],
        "head_hash": checkpoint["event_hash"],
        "auto_anchor": True,
    }
    _print_output(output, args.json)
    return 0


def _cmd_cloud_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = _bac_config(read_events(_bac_path(root, args.bac_file)))
    anchor_url = args.url or config.get("anchor.url")
    token = args.token or os.getenv("BAC_ANCHOR_API_TOKEN") or (
        _cloud_token_for(anchor_url) if isinstance(anchor_url, str) else None
    )
    output: dict[str, Any] = {
        "status": "configured" if anchor_url else "not_configured",
        "url": anchor_url,
        "ledger_id": config.get("anchor.ledger_id"),
        "anchor_required": bool(config.get("anchor.require")),
        "auto_anchor": bool(config.get("cloud.auto_anchor")),
        "token_available": bool(token),
    }
    if isinstance(anchor_url, str) and token:
        output["cloud"] = _get_cloud_json(anchor_url, "/api/v1/cloud/me", token=token)
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


def _validate_record_payload_against_ledger(
    event_type: str,
    payload: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    if event_type != "human_approval" or "approves_event_hash" not in payload:
        return
    approved_hash = payload.get("approves_event_hash")
    if not is_sha256(approved_hash):
        raise ValueError("payload.approves_event_hash must be sha256:<hex>")
    previous_hashes = {
        event.get("event_hash")
        for event in events
        if isinstance(event.get("event_hash"), str)
    }
    if approved_hash not in previous_hashes:
        raise ValueError("payload.approves_event_hash must reference an earlier event_hash in this ledger")


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


def _append_config_event(
    root: Path,
    bac_path: Path,
    events: list[dict[str, Any]],
    config: dict[str, Any],
    summary: str,
) -> dict[str, Any]:
    event = build_record_event(
        root=root,
        prev_event_hash=events[-1]["event_hash"],
        event_type="verification",
        source_type="system",
        summary=summary,
        trust_level="observed",
        actor=default_actor("bac", "system_tool"),
        payload={"bac_config": config},
    )
    append_event(bac_path, event)
    return event


def _anchor_push_current(
    root: Path,
    bac_path: Path,
    *,
    public_key: str | None = None,
    token: str | None = None,
    allow_insecure: bool = False,
) -> dict[str, Any]:
    events = read_events(bac_path)
    if not events:
        raise FileNotFoundError(f"BAC file is empty or missing: {bac_path}")
    config = _bac_config(events)
    anchor_url = config.get("anchor.url")
    if not isinstance(anchor_url, str) or not anchor_url:
        raise ValueError("anchor.url is not configured; run bac config set anchor.url <url>")
    _validate_anchor_push_url(anchor_url, allow_insecure=allow_insecure or bool(config.get("anchor.allow_insecure")))

    request = _build_request_for_current_head(events)
    resolved_token = token or os.getenv("BAC_ANCHOR_API_TOKEN") or _cloud_token_for(anchor_url)
    receipt = _post_anchor_request(anchor_url, request, token=resolved_token)
    resolved_public_key = public_key or _fetch_public_key(anchor_url, receipt.get("key_id"))
    return _append_anchor_checkpoint(root, bac_path, events, receipt, resolved_public_key)


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
        client_summary=_cloud_client_summary(events),
    )


def _cloud_client_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    trust_counts: dict[str, int] = {}
    for event in events:
        source = event.get("source_type")
        trust = event.get("trust_level")
        if isinstance(source, str):
            source_counts[source] = source_counts.get(source, 0) + 1
        if isinstance(trust, str):
            trust_counts[trust] = trust_counts.get(trust, 0) + 1
    head = events[-1]
    return {
        "event_count": len(events),
        "source_counts": source_counts,
        "trust_counts": trust_counts,
        "head_event_id": head.get("event_id"),
        "head_event_type": head.get("event_type"),
        "head_source_type": head.get("source_type"),
        "head_trust_level": head.get("trust_level"),
        "head_created_at": head.get("created_at"),
        "redaction_count": sum(
            len(event.get("redactions", [])) for event in events if isinstance(event.get("redactions"), list)
        ),
    }


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


def _post_anchor_request(anchor_url: str, request: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    return _post_cloud_json(anchor_url, "/api/v1/anchors", request, token=token)


def _post_cloud_json(base_url: str, path: str, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    http_request = urllib.request.Request(
        url,
        data=canonical_json(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    return _open_json(http_request, "anchor service response")


def _get_cloud_json(base_url: str, path: str, token: str | None = None) -> dict[str, Any]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    http_request = urllib.request.Request(base_url.rstrip("/") + path, headers=headers, method="GET")
    return _open_json(http_request, "cloud service response")


def _open_json(http_request: urllib.request.Request, label: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(http_request, timeout=15) as response:
            return _json_response(response.read(), label)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"cloud request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"cloud request failed: {exc}") from exc


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


def _validate_anchor_push_url(anchor_url: str, allow_insecure: bool = False) -> None:
    parsed = urlparse(anchor_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsafe anchor.url: only http(s) URLs with a hostname are supported")
    if allow_insecure:
        return
    if parsed.scheme != "https":
        raise ValueError("unsafe anchor.url: https is required unless --allow-insecure-anchor-url is set")

    hostname = parsed.hostname
    if hostname.lower() == "localhost":
        raise ValueError("unsafe anchor.url: local hostnames are not allowed by default")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        for resolved in _resolve_host_addresses(hostname, parsed.port):
            if _is_unsafe_address(resolved):
                raise ValueError("unsafe anchor.url: hostname resolves to a private, local, reserved, or multicast address")
        return
    if _is_unsafe_address(address):
        raise ValueError("unsafe anchor.url: private, local, reserved, or multicast addresses are not allowed")


def _resolve_host_addresses(hostname: str, port: int | None) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"unsafe anchor.url: hostname could not be resolved: {hostname}") from exc
    addresses = []
    for family, _type, _proto, _canonname, sockaddr in infos:
        if family in {socket.AF_INET, socket.AF_INET6}:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
    if not addresses:
        raise ValueError(f"unsafe anchor.url: hostname did not resolve to an IP address: {hostname}")
    return addresses


def _is_unsafe_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _json_response(raw: bytes, label: str) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _credentials_path() -> Path:
    configured = os.getenv("BAC_CLOUD_CREDENTIALS_FILE")
    if configured:
        return Path(configured)
    config_home = os.getenv("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "bac" / "credentials.json"


def _store_cloud_token(url: str, token: str, email: Any = None) -> None:
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    credentials = _read_credentials(path)
    services = credentials.setdefault("services", {})
    services[url.rstrip("/")] = {"token": token, "email": email}
    path.write_text(json.dumps(credentials, indent=2, sort_keys=True), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _cloud_token_for(url: str) -> str | None:
    credentials = _read_credentials(_credentials_path())
    service = credentials.get("services", {}).get(url.rstrip("/"))
    if isinstance(service, dict) and isinstance(service.get("token"), str):
        return service["token"]
    return None


def _read_credentials(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"services": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"services": {}}
    if not isinstance(value, dict):
        return {"services": {}}
    if not isinstance(value.get("services"), dict):
        value["services"] = {}
    return value


def _parse_bool(raw: str) -> bool:
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean configuration values must be true or false")


def _print_output(output: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(canonical_json(output))
    else:
        for key, value in output.items():
            print(f"{key}: {value}")
