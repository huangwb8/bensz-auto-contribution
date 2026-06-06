"""Read and append BAC v2 ZIP container files."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator
from zipfile import BadZipFile, ZipFile

from bac.core import container
from bac.core.canonicalize import canonical_json
from bac.core.container import (
    MANIFEST_PATH,
    ZIP_COMPRESSION,
    build_manifest,
    duplicate_names,
    event_path,
    event_sequence,
)
from bac.core.hash_chain import compute_event_hash
from bac.core.verify import verify_bac_file

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback keeps reads/writes functional.
    fcntl = None  # type: ignore[assignment]


DEFAULT_BAC_FILE = "project.bac"


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    if path.stat().st_size > container.MAX_BAC_BYTES:
        raise ValueError(f"BAC file exceeds maximum size of {container.MAX_BAC_BYTES} bytes: {path}")
    try:
        with ZipFile(path, "r") as archive:
            names = archive.namelist()
            duplicates = duplicate_names(names)
            if duplicates:
                raise ValueError(f"BAC container contains duplicate entries: {', '.join(duplicates)}")

            event_members = sorted(
                (event_sequence(name), name)
                for name in names
                if event_sequence(name) is not None
            )
            if len(event_members) > container.MAX_EVENT_COUNT:
                raise ValueError(
                    f"BAC container has too many event members: {len(event_members)} > {container.MAX_EVENT_COUNT}"
                )
            for sequence, name in event_members:
                if sequence is None:
                    continue
                info = archive.getinfo(name)
                if info.file_size > container.MAX_MEMBER_UNCOMPRESSED_BYTES:
                    raise ValueError(
                        f"{name}: uncompressed size exceeds limit of "
                        f"{container.MAX_MEMBER_UNCOMPRESSED_BYTES} bytes"
                    )
                try:
                    events.append(json.loads(archive.read(name).decode("utf-8")))
                except UnicodeDecodeError as exc:
                    raise ValueError(f"{name}: content must be UTF-8 JSON") from exc
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{name}: invalid JSON: {exc.msg}") from exc
    except BadZipFile as exc:
        raise ValueError(f"BAC file is not a valid v2 ZIP container: {path}") from exc
    return events


def current_head_hash(path: Path) -> str | None:
    events = read_events(path)
    if not events:
        return None
    return events[-1].get("event_hash")


def initialize_bac_file(path: Path, genesis_event: dict[str, Any], force: bool = False) -> None:
    if path.exists() and path.stat().st_size > 0 and not force:
        raise FileExistsError(f"BAC file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(genesis_event)
    with ZipFile(path, "w", compression=ZIP_COMPRESSION) as archive:
        archive.writestr(MANIFEST_PATH, canonical_json(manifest))
        archive.writestr(event_path(1), canonical_json(genesis_event))


@contextmanager
def locked_bac_file(path: Path) -> Iterator[None]:
    """Serialize writers for a BAC container in this process group."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def append_event(
    path: Path,
    event: dict[str, Any],
    verify_existing: bool = True,
    *,
    allow_stale_head_rebase: bool = False,
    lock: bool = True,
) -> None:
    if lock:
        with locked_bac_file(path):
            _append_event_unlocked(
                path,
                event,
                verify_existing=verify_existing,
                allow_stale_head_rebase=allow_stale_head_rebase,
            )
        return

    _append_event_unlocked(
        path,
        event,
        verify_existing=verify_existing,
        allow_stale_head_rebase=allow_stale_head_rebase,
    )


def _append_event_unlocked(
    path: Path,
    event: dict[str, Any],
    *,
    verify_existing: bool,
    allow_stale_head_rebase: bool,
) -> None:
    if not path.exists():
        raise FileNotFoundError(f"BAC file does not exist: {path}")
    if verify_existing:
        report = verify_bac_file(path)
        if report.errors:
            raise ValueError(f"cannot append to invalid BAC file: {'; '.join(report.errors)}")
    events = read_events(path)
    if not events:
        raise ValueError(f"BAC file contains no events: {path}")
    current_hash = events[-1].get("event_hash")
    if not isinstance(current_hash, str):
        raise ValueError("current BAC head is missing event_hash")
    if event.get("prev_event_hash") != current_hash:
        if allow_stale_head_rebase and _can_rebase_stale_event(event):
            _rebase_event_to_head(event, current_hash)
        else:
            raise ValueError(
                "event prev_event_hash does not match current BAC head: "
                f"expected {current_hash}, got {event.get('prev_event_hash')}"
            )
    if event.get("prev_event_hash") != current_hash:
        raise ValueError(
            "event prev_event_hash does not match current BAC head: "
            f"expected {current_hash}, got {event.get('prev_event_hash')}"
        )
    rewrite_events_atomic(path, [*events, event])


def rewrite_events_atomic(path: Path, events: list[dict[str, Any]]) -> None:
    """Rewrite event members via a verified same-directory replacement.

    ZIP append mode mutates the original file in place, including the central
    directory tail. A process interruption at that point can leave a readable
    container whose final member has invalid compressed data. Rebuilding into a
    sibling temp file and replacing only after container-integrity validation
    keeps the previous ledger intact on write failure.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp_path = Path(raw_tmp)
    try:
        with ZipFile(path, "r") as source, ZipFile(tmp_path, "w", compression=ZIP_COMPRESSION) as target:
            names = source.namelist()
            if MANIFEST_PATH not in names:
                raise ValueError(f"container missing {MANIFEST_PATH}")
            for name in names:
                if event_sequence(name) is None:
                    info = source.getinfo(name)
                    if info.file_size > container.MAX_MEMBER_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            f"{name}: uncompressed size exceeds limit of "
                            f"{container.MAX_MEMBER_UNCOMPRESSED_BYTES} bytes"
                        )
                    target.writestr(name, source.read(name))
            for index, item in enumerate(events, start=1):
                target.writestr(event_path(index), canonical_json(item))

        _copymode(path, tmp_path)
        _fsync_file(tmp_path)
        _validate_rewritten_container(tmp_path, expected_events=len(events))
        os.replace(tmp_path, path)
        _fsync_parent(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _can_rebase_stale_event(event: dict[str, Any]) -> bool:
    if event.get("event_type") in {"genesis", "checkpoint"}:
        return False
    if event.get("trust_level") in {"signed", "anchored"}:
        return False
    if event.get("signature") is not None:
        return False
    payload = event.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("anchor"), dict):
        return False
    return True


def _rebase_event_to_head(event: dict[str, Any], current_hash: str) -> None:
    before = deepcopy(event)
    event["prev_event_hash"] = current_hash
    event["event_hash"] = compute_event_hash(event)
    for key in (set(before) | set(event)) - {"prev_event_hash", "event_hash"}:
        if before.get(key) != event.get(key):
            raise ValueError("stale event rebase changed fields beyond prev_event_hash and event_hash")


def _validate_rewritten_container(path: Path, *, expected_events: int) -> None:
    try:
        with ZipFile(path, "r") as archive:
            bad_member = archive.testzip()
    except BadZipFile as exc:
        raise ValueError(f"rewritten BAC file is not a valid ZIP container: {path}") from exc
    if bad_member is not None:
        raise ValueError(f"rewritten BAC container has invalid member data: {bad_member}")
    events = read_events(path)
    if len(events) != expected_events:
        raise ValueError(f"rewritten BAC container has {len(events)} events, expected {expected_events}")


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        return


def _copymode(source: Path, target: Path) -> None:
    try:
        target.chmod(source.stat().st_mode & 0o777)
    except OSError:
        return


def _fsync_parent(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd: int | None = None
    try:
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
    except OSError:
        return
    finally:
        if fd is not None:
            os.close(fd)
