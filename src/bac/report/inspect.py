"""Timeline rendering for BAC ledgers."""

from __future__ import annotations

from typing import Any


def timeline(events: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    selected = events[-limit:] if limit else events
    return [
        {
            "created_at": event.get("created_at"),
            "event_type": event.get("event_type"),
            "source_type": event.get("source_type"),
            "trust_level": event.get("trust_level"),
            "summary": event.get("payload", {}).get("summary", ""),
            "event_hash": event.get("event_hash"),
        }
        for event in selected
    ]
