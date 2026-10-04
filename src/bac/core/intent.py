"""Optional intent metadata validation; legacy events need no migration."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from bac.core.hash_chain import is_sha256

MAX_INTENT_SUMMARY_LENGTH = 4000
SUMMARY_AUTHORS = {"ai_interpretation": "ai", "human_summary": "human", "excerpt": "system"}
HUMAN_INPUT_TYPES = {"human_instruction", "human_review", "human_approval"}
FORBIDDEN_INPUT_TEXT_KEYS = {"full_text", "raw_text", "message", "prompt_text"}


def is_human_input_event(event: dict[str, Any]) -> bool:
    payload = event.get("payload")
    return (
        event.get("source_type") == "human"
        and event.get("event_type") in HUMAN_INPUT_TYPES
        and isinstance(payload, dict)
        and isinstance(payload.get("input_provenance"), dict)
    )


def validate_intent_metadata(
    event: dict[str, Any],
    previous: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return []
    errors: list[str] = []
    metadata = payload.get("intent_summary")
    evidence = event.get("evidence")
    references = [
        item for item in evidence
        if isinstance(item, dict) and item.get("type") == "intent_reference"
    ] if isinstance(evidence, list) else []
    if "intent_summary" not in payload:
        return ["intent_reference requires intent_summary metadata"] if references else []
    if not isinstance(metadata, dict):
        return ["payload.intent_summary must be an object"]
    _check_private_text_keys(payload, "payload", errors)
    _check_private_text_keys(event.get("evidence", []), "evidence", errors)
    kind = metadata.get("kind")
    if not isinstance(kind, str) or kind not in SUMMARY_AUTHORS:
        errors.append("intent_summary.kind must be ai_interpretation, human_summary or excerpt")
    elif metadata.get("author_source") != SUMMARY_AUTHORS[kind]:
        errors.append("intent_summary.author_source must match kind")
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_INTENT_SUMMARY_LENGTH:
        errors.append(f"intent summary must contain 1..{MAX_INTENT_SUMMARY_LENGTH} characters")
    if "input_event_hash" in metadata:
        target_hash = metadata.get("input_event_hash")
        if (
            event.get("source_type") != "ai"
            or event.get("event_type") != "ai_generation"
            or kind != "ai_interpretation"
        ):
            errors.append("intent supplement must be an ai_generation from ai with ai_interpretation kind")
        if not is_sha256(target_hash):
            errors.append("intent_summary.input_event_hash must be sha256:<hex>")
        elif previous is not None:
            target = previous.get(target_hash)
            if not target or not is_human_input_event(target):
                errors.append("intent_summary.input_event_hash must reference a previous human input event in this ledger")
    elif not is_human_input_event(event):
        errors.append("intent_summary requires a human input event or a linked AI supplement")
    if kind == "excerpt" and references:
        errors.append("excerpt cannot claim intent reference evidence")
    for reference in references:
        path = reference.get("path")
        if (
            not isinstance(path, str) or not path or path == "." or "\\" in path
            or PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive
            or ".." in PurePosixPath(path).parts
        ):
            errors.append("intent_reference.path must be a relative project file path")
        if not is_sha256(reference.get("hash")):
            errors.append("intent_reference.hash must be sha256:<hex>")
        if reference.get("source_type") != "tool" or reference.get("redacted") is not True:
            errors.append("intent_reference must be marked source_type=tool and redacted=true")
        if "locator" in reference and (
            not isinstance(reference["locator"], str) or not reference["locator"].strip()
            or len(reference["locator"]) > MAX_INTENT_SUMMARY_LENGTH
        ):
            errors.append("intent_reference.locator must be a non-empty string of at most 4000 characters")
    return errors


def _check_private_text_keys(value: Any, label: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_INPUT_TEXT_KEYS:
                errors.append(f"{label}.{key}: full human input text is not allowed in intent records")
            _check_private_text_keys(item, f"{label}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_private_text_keys(item, f"{label}[{index}]", errors)
