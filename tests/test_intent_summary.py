"""Intent evidence, attribution, append boundaries and controlled host examples."""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bac.adapters.cli import main
from bac.core.hash_chain import attach_event_hash, sha256_digest
from bac.core.verify import verify_bac_file, verify_events
from bac.report.inspect import timeline
from bac.service.evidence import collect_intent_references, human_input_message_hash
from bac.service.event_builder import build_genesis_event, build_human_input_event, build_record_event
from bac.storage.bac_file import append_event, initialize_bac_file, read_events


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "project.bac"
    genesis = build_genesis_event(tmp_path)
    initialize_bac_file(path, genesis)
    return tmp_path, path, genesis


def human(root, head, **kwargs):
    return build_human_input_event(root=root, prev_event_hash=head, text="按报告修复全部 P0–P2，保留已有功能。", channel="ai_tool_user_message", classification="instruction", **kwargs)


def supplement(root, head, target, summary="AI 提炼：修复并发丢失事件，保留已有功能。", **kwargs):
    return build_record_event(root=root, prev_event_hash=head, event_type="ai_generation", source_type="ai", summary=summary,
                              payload={"intent_summary": {"kind": "ai_interpretation", "author_source": "ai", "input_event_hash": target}}, **kwargs)


def cli(root, capsys, *arguments):
    code = main(["--root", str(root), "--bac-file", "project.bac", *arguments, "--json"])
    output = capsys.readouterr()
    return code, json.loads(output.out) if output.out else output.err


def test_explicit_summary_is_complete_redacted_and_hash_uses_input(ledger):
    root, path, genesis = ledger
    summary = "目标：解决并发丢失事件、证据版本漂移和错误定位；" * 15 + "尾部验收：保留已有功能。 token=super-secret"
    event = human(root, genesis["event_hash"], summary=summary)
    append_event(path, event)
    assert len(event["payload"]["summary"]) > 96
    assert event["payload"]["summary"].endswith("[REDACTED]")
    assert "尾部验收：保留已有功能。" in event["payload"]["summary"]
    assert "super-secret" not in json.dumps(event)
    assert event["payload"]["intent_summary"] == {"kind": "ai_interpretation", "author_source": "ai"}
    different = human(root, genesis["event_hash"], summary="不同解释", summary_source="human")
    assert different["payload"]["input_provenance"]["message_hash"] == event["payload"]["input_provenance"]["message_hash"]
    assert different["payload"]["intent_summary"]["kind"] == "human_summary"
    assert not verify_bac_file(path).errors


@pytest.mark.parametrize("kwargs", [{"summary": ""}, {"summary": "  "}, {"summary": "a" * 4001}, {"summary": "a", "summary_source": "tool"}, {"summary": "a", "summary_source": ""}, {"summary_source": "ai"}])
def test_invalid_explicit_summaries_fail(ledger, kwargs):
    root, _, genesis = ledger
    with pytest.raises(ValueError):
        human(root, genesis["event_hash"], **kwargs)


def test_limit_is_public_and_no_truncation_at_boundary(ledger):
    root, _, genesis = ledger
    event = human(root, genesis["event_hash"], summary="目" * 4000)
    assert event["payload"]["summary"] == "目" * 4000
    with pytest.raises(ValueError):
        supplement(root, genesis["event_hash"], genesis["event_hash"], summary="目" * 4001)


def test_supplement_redacts_secrets_and_rejects_full_prompt_fields(ledger):
    root, path, genesis = ledger
    original = human(root, genesis["event_hash"])
    append_event(path, original)
    interpretation = supplement(root, original["event_hash"], original["event_hash"], summary="修复分页。 token=private-token", evidence=[{"type": "host_observation", "excerpt": "password=private-password"}])
    append_event(path, interpretation)
    assert "private-token" not in json.dumps(interpretation)
    assert "private-password" not in json.dumps(interpretation)
    with pytest.raises(ValueError, match="full human input text"):
        supplement(root, interpretation["event_hash"], original["event_hash"], evidence=[{"type": "host_observation", "raw_text": "private prompt"}])


def test_cli_invalid_intent_options_do_not_modify_ledger(ledger, capsys, monkeypatch):
    root, path, genesis = ledger
    invalid = [
        ["input", "record", "--summary-source", "ai"],
        ["input", "record", "--reference-path", "missing.md"],
        ["input", "record", "--summary", "修复问题", "--reference-json", '[{"path":"report.md"}]'],
        ["record", "--event-type", "ai_generation", "--source-type", "ai", "--summary", "修复问题", "--input-event-hash", genesis["event_hash"]],
        ["record", "--event-type", "human_instruction", "--source-type", "human", "--summary", "修复问题", "--input-event-hash", genesis["event_hash"]],
        ["record", "--event-type", "ai_generation", "--source-type", "ai", "--summary", "修复问题", "--reference-path", "missing.md"],
    ]
    before = path.read_bytes()
    for arguments in invalid:
        monkeypatch.setattr(sys, "stdin", io.StringIO("修复问题"))
        code, _ = cli(root, capsys, *arguments)
        assert code == 2
        assert path.read_bytes() == before


def test_excerpt_and_legacy_reading(ledger):
    root, path, genesis = ledger
    event = human(root, genesis["event_hash"])
    assert event["payload"]["intent_summary"] == {"kind": "excerpt", "author_source": "system"}
    legacy = copy.deepcopy(event)
    del legacy["payload"]["intent_summary"]
    legacy = attach_event_hash(legacy)
    append_event(path, legacy)
    assert not verify_bac_file(path).errors
    assert "intent_summary" not in timeline(read_events(path), source_type="human")[0]


def test_retry_preserves_first_event_and_returns_original_input_hash(ledger, capsys, monkeypatch):
    root, path, _ = ledger
    message = "修复报告并保留已有功能。"
    monkeypatch.setattr(sys, "stdin", io.StringIO(message))
    code, first = cli(root, capsys, "input", "record", "--summary", "修复并发问题。")
    assert code == 0
    before = path.read_bytes()
    monkeypatch.setattr(sys, "stdin", io.StringIO(message))
    code, retry = cli(root, capsys, "input", "record", "--summary", "新的解释", "--reference-path", "missing.md")
    assert code == 0 and retry["status"] == "skipped"
    assert retry["input_event_hash"] == first["input_event_hash"]
    assert retry["event_id"] == first["event_id"]
    assert path.read_bytes() == before
    code, added = cli(root, capsys, "record", "--event-type", "ai_generation", "--source-type", "ai", "--summary", "新的解释", "--input-event-hash", retry["input_event_hash"])
    assert code == 0
    code, items = cli(root, capsys, "inspect", "--human")
    assert code == 0 and len(items) == 1
    assert items[0]["summary"] == "修复并发问题。"
    assert items[0]["source_type"] == "human"
    assert items[0]["intent_supplements"][0]["source_type"] == "ai"
    assert items[0]["intent_supplements"][0]["event_hash"] == added["head_hash"]


@pytest.mark.parametrize("target_kind", ["missing", "genesis", "human-without-input", "ai", "future", "other-ledger"])
def test_supplement_rejects_invalid_targets_without_mutating_ledger(ledger, target_kind):
    root, path, genesis = ledger
    if target_kind == "genesis":
        target = genesis["event_hash"]
    elif target_kind in {"human-without-input", "ai"}:
        source = "ai" if target_kind == "ai" else "human"
        event = build_record_event(root=root, prev_event_hash=genesis["event_hash"], source_type=source, event_type="ai_generation" if source == "ai" else "human_instruction", summary="非人类输入事件")
        append_event(path, event)
        target = event["event_hash"]
    elif target_kind in {"future", "other-ledger"}:
        target = human(root, genesis["event_hash"])["event_hash"]
    else:
        target = "sha256:" + "0" * 64
    events = read_events(path)
    draft = supplement(root, events[-1]["event_hash"], target)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="previous human input"):
        append_event(path, draft)
    assert path.read_bytes() == before
    assert any("previous human input" in error for error in verify_events([*events, draft]).errors)


def test_corrected_understanding_appends_and_preserves_sources_and_history(ledger, capsys):
    root, path, genesis = ledger
    original = human(root, genesis["event_hash"])
    append_event(path, original)
    first = supplement(root, original["event_hash"], original["event_hash"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    first["created_at"] = tomorrow.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    first = attach_event_hash(first)
    append_event(path, first)
    second = supplement(root, first["event_hash"], original["event_hash"], summary="补正：范围还包含阶段 B 的错误定位。")
    second["created_at"] = first["created_at"]
    second = attach_event_hash(second)
    append_event(path, second)
    events = read_events(path)
    assert events[1] == original
    items = timeline(events, source_type="human", limit=1, on=original["created_at"][:10])
    assert len(items) == 1 and len(items[0]["intent_supplements"]) == 2
    assert items[0]["intent_supplements"][0]["created_at"][:10] != original["created_at"][:10]
    assert [item["source_type"] for item in timeline(events)] == ["system", "human", "ai", "ai"]
    assert not verify_bac_file(path).errors
    assert main(["--root", str(root), "--bac-file", "project.bac", "inspect", "--human"]) == 0
    display = capsys.readouterr().out
    assert "excerpt; intent not yet interpreted" in display
    assert display.count("AI interpretation; not human confirmation") == 2


def test_reference_matches_bytes_and_host_reading_and_redacts_locator(ledger):
    root, path, genesis = ledger
    report = root / "report.md"
    content = b"# P0\r\nLost concurrent events\r\n"
    report.write_bytes(content)
    digest = sha256_digest(content)
    event = human(root, genesis["event_hash"], summary="修复并发丢失事件。", references=[{"path": "report.md", "hash": digest, "locator": "P0 token=secret-value"}])
    append_event(path, event)
    assert event["evidence"][-1] == {"type": "intent_reference", "source_type": "tool", "path": "report.md", "hash": digest, "redacted": True, "locator": "P0 [REDACTED]"}
    report.write_text("changed")
    with pytest.raises(ValueError, match="host's reading"):
        human(root, event["event_hash"], summary="修复并发丢失事件。", references=[{"path": "report.md", "hash": digest}])
    # Verification checks recorded evidence, not today's version of a report.
    assert not verify_bac_file(path).errors


@pytest.mark.parametrize("kind", ["missing", "directory", "outside", "symlink", "fifo"])
def test_reference_failures_do_not_create_observations(ledger, kind):
    root, _, _ = ledger
    outside = root.parent / (root.name + "-outside.md")
    outside.write_text("private")
    if kind == "directory":
        raw = "."
    elif kind == "outside":
        raw = "../" + outside.name
    elif kind == "symlink":
        (root / "link.md").symlink_to(outside)
        raw = "link.md"
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("POSIX FIFO test")
        os.mkfifo(root / "fifo")
        raw = "fifo"
    else:
        raw = "missing.md"
    with pytest.raises((ValueError, OSError)):
        collect_intent_references(root, [{"path": raw}])


def test_reference_permission_error_is_propagated(ledger, monkeypatch):
    root, _, _ = ledger
    (root / "report.md").write_text("report")
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(PermissionError):
        collect_intent_references(root, [{"path": "report.md"}])


def test_reference_directory_symlink_swap_is_rejected_before_read(ledger, monkeypatch):
    root, _, _ = ledger
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("descriptor-relative no-follow open is unavailable")
    inside = root / "reports"
    inside.mkdir()
    (inside / "report.md").write_text("authorized")
    outside = root.parent / (root.name + "-outside")
    outside.mkdir()
    (outside / "report.md").write_text("private")
    original_open = os.open

    def swapping_open(path, flags, *args, **kwargs):
        if path == "reports":
            inside.rename(root / "old-reports")
            inside.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, swapping_open})
    with pytest.raises(OSError):
        collect_intent_references(root, [{"path": "reports/report.md"}])


@pytest.mark.parametrize("change", ["edit", "replace", "symlink-swap"])
def test_reference_changes_during_read_fail(ledger, monkeypatch, change):
    root, _, _ = ledger
    report = root / "report.md"
    report.write_text("original")
    original_fstat = os.fstat
    calls = 0

    def changing_fstat(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "edit":
                report.write_text("edited report")
            elif change == "replace":
                replacement = root / "replacement.md"
                replacement.write_text("replacement")
                replacement.replace(report)
            else:
                outside = root.parent / (root.name + "-outside.md")
                outside.write_text("outside")
                report.unlink()
                report.symlink_to(outside)
        return original_fstat(descriptor)

    monkeypatch.setattr(os, "fstat", changing_fstat)
    with pytest.raises(ValueError, match="changed during"):
        collect_intent_references(root, [{"path": "report.md"}])


@pytest.mark.parametrize("mutation", ["summary", "reference", "link"])
def test_tampering_intent_fields_is_detected(ledger, mutation):
    root, path, genesis = ledger
    (root / "report.md").write_text("P0: lost events")
    original = human(root, genesis["event_hash"], summary="修复并发丢失事件。", references=[{"path": "report.md"}])
    append_event(path, original)
    interpretation = supplement(root, original["event_hash"], original["event_hash"])
    append_event(path, interpretation)
    events = read_events(path)
    if mutation == "summary":
        events[1]["payload"]["summary"] = "篡改范围"
    elif mutation == "reference":
        events[1]["evidence"][-1]["hash"] = "sha256:" + "0" * 64
    else:
        events[2]["payload"]["intent_summary"]["input_event_hash"] = genesis["event_hash"]
    assert any("event_hash mismatch" in error for error in verify_events(events).errors)


@pytest.mark.parametrize("mutation", ["author", "kind", "path", "windows-path", "hash", "source", "locator", "summary-empty", "metadata-null", "non-ai-supplement"])
def test_malformed_intent_is_rejected_even_with_recomputed_hash(ledger, mutation):
    root, path, genesis = ledger
    (root / "report.md").write_text("P0")
    event = human(root, genesis["event_hash"], summary="修复 P0", references=[{"path": "report.md"}])
    metadata = event["payload"]["intent_summary"]
    reference = event["evidence"][-1]
    if mutation == "author":
        metadata["author_source"] = "human"
    elif mutation == "kind":
        metadata["kind"] = []
    elif mutation == "path":
        reference["path"] = "../outside"
    elif mutation == "windows-path":
        reference["path"] = "C:\\outside"
    elif mutation == "hash":
        reference["hash"] = "bad-hash"
    elif mutation == "source":
        reference["source_type"] = "human"
    elif mutation == "locator":
        reference["locator"] = []
    elif mutation == "summary-empty":
        event["payload"]["summary"] = " "
    elif mutation == "metadata-null":
        event["payload"]["intent_summary"] = None
    else:
        metadata["input_event_hash"] = genesis["event_hash"]
    event = attach_event_hash(event)
    assert verify_events([genesis, event]).errors
    with pytest.raises(ValueError):
        append_event(path, event)


CASES = json.loads((Path(__file__).parent / "fixtures/human-intent.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[case["case"] for case in CASES])
def test_controlled_host_summaries_keep_actual_scope_and_reference_version(ledger, case):
    root, path, genesis = ledger
    references = []
    if "report" in case:
        (root / "report.md").write_text(case["report"], encoding="utf-8")
        references = [{"path": "report.md", "hash": sha256_digest(case["report"].encode()), "locator": case["locator"]}]
    event = build_human_input_event(root=root, prev_event_hash=genesis["event_hash"], text=case["message"], channel="ai_tool_user_message", classification="instruction", summary=case["summary"], references=references)
    append_event(path, event)
    item = timeline(read_events(path), source_type="human")[0]
    assert item["summary"] == case["summary"]
    assert item["event_type"] == "human_instruction"
    assert item["intent_summary"] == {"kind": "ai_interpretation", "author_source": "ai"}
    assert item["input_provenance"]["message_hash"] == human_input_message_hash(case["message"])
    assert len(item["intent_references"]) == len(references)
    assert not verify_bac_file(path).errors


def test_paired_reports_share_input_but_have_distinct_goals_and_evidence(ledger):
    root, _, genesis = ledger
    events = []
    for case in CASES[:2]:
        (root / "report.md").write_text(case["report"], encoding="utf-8")
        events.append(human(root, genesis["event_hash"], summary=case["summary"], references=[{"path": "report.md"}]))
    assert events[0]["payload"]["input_provenance"]["message_hash"] == events[1]["payload"]["input_provenance"]["message_hash"]
    assert events[0]["payload"]["summary"] != events[1]["payload"]["summary"]
    assert events[0]["evidence"][-1]["hash"] != events[1]["evidence"][-1]["hash"]


def test_concurrent_capture_and_supplements_keep_chain_and_dedup(ledger):
    root, path, _ = ledger
    command = [sys.executable, "-m", "bac", "--root", str(root), "--bac-file", "project.bac"]
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def capture(_):
        result = subprocess.run([*command, "input", "record", "--session-id", "same-session", "--message-index", "1", "--json"], input="修复全部 P0–P2", text=True, capture_output=True, env=environment)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with ThreadPoolExecutor(max_workers=4) as pool:
        captures = list(pool.map(capture, range(4)))
    assert sum(result["recorded"] for result in captures) == 1
    target = captures[0]["input_event_hash"]

    def explain(index):
        result = subprocess.run([*command, "record", "--event-type", "ai_generation", "--source-type", "ai", "--input-event-hash", target, "--summary", f"理解补充 {index}", "--json"], text=True, capture_output=True, env=environment)
        assert result.returncode == 0, result.stderr

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(explain, range(4)))
    assert len(read_events(path)) == 6
    assert not verify_bac_file(path).errors
