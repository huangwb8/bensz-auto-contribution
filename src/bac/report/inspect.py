"""Timeline rendering for BAC ledgers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from bac.core.schema import parse_created_at


def timeline(
    events: list[dict[str, Any]],
    limit: int | None = None,
    source_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    on: str | None = None,
) -> list[dict[str, Any]]:
    filtered = filter_events(events, source_type=source_type, since=since, until=until, on=on)
    selected = filtered[-limit:] if limit else filtered
    items = []
    for event in selected:
        payload = event.get("payload", {})
        provenance = payload.get("input_provenance") if isinstance(payload, dict) else None
        item = {
            "created_at": event.get("created_at"),
            "event_type": event.get("event_type"),
            "source_type": event.get("source_type"),
            "trust_level": event.get("trust_level"),
            "summary": payload.get("summary", "") if isinstance(payload, dict) else "",
            "event_hash": event.get("event_hash"),
        }
        if isinstance(provenance, dict):
            item["input_provenance"] = _input_provenance_summary(provenance)
        items.append(item)
    return items


def filter_events(
    events: list[dict[str, Any]],
    source_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    on: str | None = None,
) -> list[dict[str, Any]]:
    if on and (since or until):
        raise ValueError("--on cannot be combined with --since or --until")

    start = _parse_lower_bound(since, "--since") if since else None
    end = _parse_upper_bound(until, "--until") if until else None
    if on:
        start = _parse_date_start(on, "--on")
        end = start + timedelta(days=1)
    if start and end and start >= end:
        raise ValueError("time filter requires --since to be earlier than --until")

    result = []
    for event in events:
        if source_type and event.get("source_type") != source_type:
            continue
        created_at = event.get("created_at")
        if start or end:
            if not isinstance(created_at, str):
                continue
            timestamp = _event_created_at(created_at)
            if start and timestamp < start:
                continue
            if end and timestamp >= end:
                continue
        result.append(event)
    return result


def _event_created_at(raw: str) -> datetime:
    try:
        return parse_created_at(raw)
    except ValueError as exc:
        raise ValueError(f"event created_at is not a valid UTC timestamp: {raw}") from exc


def _parse_lower_bound(raw: str, label: str) -> datetime:
    parsed, _is_date_only = _parse_cli_datetime(raw, label)
    return parsed


def _parse_upper_bound(raw: str, label: str) -> datetime:
    parsed, is_date_only = _parse_cli_datetime(raw, label)
    if is_date_only:
        return parsed + timedelta(days=1)
    return parsed


def _parse_date_start(raw: str, label: str) -> datetime:
    try:
        value = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be a date in YYYY-MM-DD format") from exc
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _parse_cli_datetime(raw: str, label: str) -> tuple[datetime, bool]:
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        pass
    else:
        return datetime.combine(value, time.min, tzinfo=timezone.utc), True

    normalized = raw.removesuffix("Z") + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be a date or ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), False


def _input_provenance_summary(provenance: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "format",
        "channel",
        "host",
        "session_id",
        "message_index",
        "message_hash",
        "source_path",
        "start_line",
        "end_line",
        "classification",
        "recorded_full_text",
    )
    return {key: provenance[key] for key in keys if key in provenance}
