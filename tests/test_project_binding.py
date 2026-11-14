from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from bac.adapters import cli
from bac.core.container import MANIFEST_PATH, event_path
from bac.core.hash_chain import attach_event_hash
from bac.core.verify import verify_bac_file
from bac.service.event_builder import build_record_event
from bac.storage.bac_file import DEFAULT_BAC_FILE, append_event, read_events


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def invoke(root, capsys, *args):
    code = cli.main(["--root", str(root), *args, "--json"])
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out else captured.err


@pytest.fixture
def repo(tmp_path, capsys):
    git(tmp_path, "init")
    assert invoke(tmp_path, capsys, "init", "--mode", "local")[0] == 0
    return tmp_path, tmp_path / DEFAULT_BAC_FILE


@pytest.mark.parametrize("existing_origin", [False, True])
def test_remote_changes_keep_identity_and_historical_bytes(tmp_path, capsys, existing_origin):
    git(tmp_path, "init")
    if existing_origin:
        git(tmp_path, "remote", "add", "origin", "https://example.invalid/old.git")
    assert invoke(tmp_path, capsys, "init", "--mode", "local")[0] == 0
    path = tmp_path / DEFAULT_BAC_FILE
    genesis = read_events(path)[0]
    with ZipFile(path) as archive:
        historical = {name: archive.read(name) for name in archive.namelist()}
    git(tmp_path, "remote", "set-url" if existing_origin else "add", "origin", "https://example.invalid/new.git")

    for args in (
        ("record", "--event-type", "ai_generation", "--source-type", "ai", "--summary", "implementation"),
        ("record", "--event-type", "checkpoint", "--source-type", "system", "--summary", "checkpoint"),
        ("config", "set", "mode", "local"),
    ):
        code, output = invoke(tmp_path, capsys, *args)
        assert code == 0, output
        assert not verify_bac_file(path).errors
    events = read_events(path)
    assert {event["project"]["root_hash"] for event in events} == {genesis["project"]["root_hash"]}
    assert all(event["project"]["git_remote"] == "https://example.invalid/new.git" for event in events[1:])
    with ZipFile(path) as archive:
        assert {name: archive.read(name) for name in historical} == historical


def test_human_input_and_import_use_existing_binding(repo, capsys, monkeypatch):
    root, path = repo
    git(root, "remote", "add", "origin", "https://example.invalid/new.git")
    monkeypatch.setattr(sys, "stdin", io.StringIO("Implement the requested feature."))
    code, output = invoke(root, capsys, "input", "record", "--summary", "实现功能", "--summary-source", "ai")
    assert code == 0, output
    (root / "Prompts.md").write_text("# Input\n\nReview the implementation.\n", encoding="utf-8")
    code, output = invoke(root, capsys, "input", "import-log")
    assert code == 0, output
    assert not verify_bac_file(path).errors
    assert len(read_events(path)) == 3


def test_relocated_project_requires_explicit_rebind_and_preserves_history(repo, capsys):
    root, path = repo
    old_genesis = read_events(path)[0]
    with ZipFile(path) as archive:
        genesis_bytes = archive.read(event_path(1))
        manifest_bytes = archive.read(MANIFEST_PATH)
    moved = root.with_name(root.name + "-moved")
    root.rename(moved)
    path = moved / DEFAULT_BAC_FILE
    before = path.read_bytes()
    args = ("record", "--event-type", "ai_generation", "--source-type", "ai", "--summary", "implementation")
    code, output = invoke(moved, capsys, *args)
    assert code == 2 and "project rebind" in output
    assert path.read_bytes() == before
    code, output = invoke(moved, capsys, "project", "rebind")
    assert code == 0, output
    event = read_events(path)[-1]
    assert event["source_type"] == "system"
    assert event["project"]["root_hash"] == old_genesis["project"]["root_hash"]
    assert event["payload"]["project_rebinding"]["from_root_path"] == str(root.resolve())
    assert event["payload"]["project_rebinding"]["to_root_path"] == str(moved.resolve())
    assert invoke(moved, capsys, *args)[0] == 0
    assert not verify_bac_file(path).errors
    with ZipFile(path) as archive:
        assert archive.read(event_path(1)) == genesis_bytes
        assert archive.read(MANIFEST_PATH) == manifest_bytes
    before = path.read_bytes()
    assert invoke(moved, capsys, "project", "rebind")[0] == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation, message", [
    ("project", "project.root_hash changed"),
    ("hash", "event_hash mismatch"),
    ("schema", "created_at"),
    ("source", "ai_generation must use source_type ai"),
    ("signed", "signed trust_level requires a valid signature"),
    ("anchored", "anchored trust_level is only valid on checkpoint events"),
])
def test_invalid_candidate_never_replaces_ledger(repo, mutation, message):
    root, path = repo
    genesis = read_events(path)[0]
    event = build_record_event(root=root, prev_event_hash=genesis["event_hash"], event_type="ai_generation", source_type="ai", summary="candidate")
    if mutation == "project":
        event["project"]["root_hash"] = "sha256:" + "0" * 64
    elif mutation == "schema":
        event["created_at"] = "not a timestamp"
    elif mutation == "source":
        event["source_type"] = "human"
    elif mutation in {"signed", "anchored"}:
        event["trust_level"] = mutation
    if mutation != "hash":
        event = attach_event_hash(event)
    else:
        event["payload"]["summary"] = "changed without rehash"
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        append_event(path, event)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
    assert not verify_bac_file(path).errors


def test_low_level_builder_can_bind_existing_ledger_after_remote_change(repo):
    root, path = repo
    genesis = read_events(path)[0]
    git(root, "remote", "add", "origin", "https://example.invalid/new.git")
    event = build_record_event(root=root, prev_event_hash=genesis["event_hash"], event_type="test_result", source_type="tool", summary="check", project_binding=genesis["project"])
    append_event(path, event)
    assert not verify_bac_file(path).errors


def test_rebind_refuses_preexisting_identity_drift_without_rewriting(repo, capsys):
    root, path = repo
    events = read_events(path)
    event = build_record_event(root=root, prev_event_hash=events[-1]["event_hash"], event_type="test_result", source_type="tool", summary="old faulty writer")
    event["project"]["root_hash"] = "sha256:" + "0" * 64
    event = attach_event_hash(event)
    # Construct an old invalid ledger without using the safe production writer.
    with ZipFile(path, "a") as archive:
        archive.writestr(event_path(2), json.dumps(event))
    before = path.read_bytes()
    code, output = invoke(root, capsys, "project", "rebind")
    assert code == 2 and "invalid BAC" in output
    assert path.read_bytes() == before


def test_git_initialization_and_commit_do_not_change_binding(tmp_path, capsys):
    assert invoke(tmp_path, capsys, "init", "--mode", "local")[0] == 0
    path = tmp_path / DEFAULT_BAC_FILE
    genesis = read_events(path)[0]
    git(tmp_path, "init")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "init")
    code, output = invoke(tmp_path, capsys, "record", "--event-type", "test_result", "--source-type", "tool", "--summary", "check")
    assert code == 0, output
    assert read_events(path)[-1]["project"]["root_hash"] == genesis["project"]["root_hash"]
    assert not verify_bac_file(path).errors


@pytest.mark.parametrize("invalid_context", ["relocation", "identity_drift"])
def test_cloud_writers_validate_before_network(repo, capsys, monkeypatch, invalid_context):
    root, path = repo
    if invalid_context == "relocation":
        moved = root.with_name(root.name + "-moved")
        root.rename(moved)
        root = moved
        path = moved / DEFAULT_BAC_FILE
        message = "project rebind"
    else:
        events = read_events(path)
        event = build_record_event(root=root, prev_event_hash=events[-1]["event_hash"], event_type="test_result", source_type="tool", summary="old faulty writer")
        event["project"]["root_hash"] = "sha256:" + "0" * 64
        with ZipFile(path, "a") as archive:
            archive.writestr(event_path(2), json.dumps(attach_event_hash(event)))
        message = "invalid BAC"
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("network must not run before project binding validation")

    monkeypatch.setattr(cli, "_post_cloud_json", forbidden)
    monkeypatch.setattr(cli, "_post_anchor_request", forbidden)
    for args in (("cloud", "link"), ("anchor", "push")):
        code, output = invoke(root, capsys, *args)
        assert code == 2 and message in output
        assert path.read_bytes() == before
