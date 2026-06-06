from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bac.core.canonicalize import canonical_json
from bac.core.container import EVENT_PATH_TEMPLATE, MANIFEST_PATH, event_path
from bac.core.hash_chain import attach_event_hash, compute_event_hash
from bac.core.schema import FORMAT_VERSION
from bac.core.verify import verify_bac_file
from bac.report.inspect import timeline
from bac.service.repair import repair_stale_tail
from bac.service.event_builder import build_genesis_event, build_record_event
from bac.service.redaction import redact_data
from bac.storage.bac_file import append_event, initialize_bac_file, read_events


class BacCoreTests(unittest.TestCase):
    def test_event_hash_is_independent_of_json_key_order(self) -> None:
        event = {
            "format": FORMAT_VERSION,
            "event_id": "example",
            "event_type": "human_instruction",
            "source_type": "human",
            "trust_level": "declared",
            "created_at": "2026-05-26T10:30:00Z",
            "project": {
                "root_path": "/tmp/project",
                "root_hash": "sha256:" + "0" * 64,
                "git_remote": None,
                "git_commit": None,
                "git_branch": None,
                "worktree_dirty": False,
            },
            "actor": {"declared_name": "user", "declared_kind": "human"},
            "payload": {"b": 2, "a": 1},
            "evidence": [],
            "redactions": [],
            "prev_event_hash": None,
            "event_hash": None,
            "signature": None,
        }
        reordered = json.loads(json.dumps(event))
        reordered["payload"] = {"a": 1, "b": 2}

        self.assertEqual(compute_event_hash(event), compute_event_hash(reordered))

    def test_verify_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            record = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="human_instruction",
                source_type="human",
                summary="Original request",
            )
            append_event(bac_file, record)

            with ZipFile(bac_file, "r") as archive:
                manifest = archive.read(MANIFEST_PATH).decode("utf-8")
                first = archive.read(event_path(1)).decode("utf-8")
                tampered = json.loads(archive.read(event_path(2)).decode("utf-8"))
            tampered["payload"]["summary"] = "Tampered request"
            with ZipFile(bac_file, "w") as archive:
                archive.writestr(MANIFEST_PATH, manifest)
                archive.writestr(event_path(1), first)
                archive.writestr(event_path(2), canonical_json(tampered))

            report = verify_bac_file(bac_file)
            self.assertEqual(report.status, "fail")
            self.assertTrue(any("event_hash mismatch" in error for error in report.errors))

    def test_init_creates_single_file_zip_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)

            self.assertTrue(bac_file.is_file())
            with ZipFile(bac_file, "r") as archive:
                self.assertEqual(sorted(archive.namelist()), [event_path(1), MANIFEST_PATH])
                manifest = json.loads(archive.read(MANIFEST_PATH))
                self.assertEqual(manifest["format"], "bac.container.v2")
                self.assertEqual(manifest["event_format"], FORMAT_VERSION)
            self.assertEqual(read_events(bac_file)[0]["event_hash"], genesis["event_hash"])

    def test_checkpointed_chain_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            record = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="ai_generation",
                source_type="ai",
                summary="Generated implementation outline",
            )
            append_event(bac_file, record)
            checkpoint = build_record_event(
                root=root,
                prev_event_hash=record["event_hash"],
                event_type="checkpoint",
                source_type="system",
                summary="Local checkpoint",
            )
            append_event(bac_file, checkpoint)

            report = verify_bac_file(bac_file)
            self.assertEqual(report.status, "warn")
            self.assertEqual(report.anchor_status, "local_checkpoint")
            self.assertTrue(any("human contributions may be underrecorded" in warning for warning in report.warnings))

    def test_append_rejects_event_built_from_stale_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            first = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="ai_generation",
                source_type="ai",
                summary="Generated implementation",
            )
            stale = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="test_result",
                source_type="tool",
                summary="Verified stale implementation",
            )
            append_event(bac_file, first)

            with self.assertRaisesRegex(ValueError, "prev_event_hash does not match current BAC head"):
                append_event(bac_file, stale)

            self.assertEqual([event["event_id"] for event in read_events(bac_file)], [genesis["event_id"], first["event_id"]])

    def test_repair_stale_tail_dry_run_plans_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            _write_stale_tail_fixture(root, bac_file)
            before = bac_file.read_bytes()

            result = repair_stale_tail(root, bac_file, apply=False)

            self.assertEqual(result["status"], "planned")
            self.assertFalse(result["apply"])
            self.assertEqual(len(result["affected_events"]), 1)
            self.assertEqual(result["affected_events"][0]["sequence"], 3)
            self.assertEqual(bac_file.read_bytes(), before)

    def test_repair_stale_tail_apply_appends_repair_record_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            original_events = _write_stale_tail_fixture(root, bac_file)
            stale_before = original_events[2]

            result = repair_stale_tail(root, bac_file, apply=True)

            self.assertEqual(result["status"], "repaired")
            self.assertTrue(result["apply"])
            self.assertIn("repair_event_id", result)
            self.assertIn("checkpoint_event_id", result)
            report = verify_bac_file(bac_file)
            self.assertIn(report.status, {"pass", "warn"})
            self.assertEqual(report.errors, [])
            repaired_events = read_events(bac_file)
            repaired_stale = repaired_events[2]
            self.assertEqual(repaired_stale["event_id"], stale_before["event_id"])
            self.assertEqual(repaired_stale["source_type"], stale_before["source_type"])
            self.assertEqual(repaired_stale["actor"], stale_before["actor"])
            self.assertEqual(repaired_stale["payload"], stale_before["payload"])
            self.assertEqual(repaired_stale["evidence"], stale_before["evidence"])
            self.assertNotEqual(repaired_stale["prev_event_hash"], stale_before["prev_event_hash"])
            self.assertNotEqual(repaired_stale["event_hash"], stale_before["event_hash"])
            self.assertEqual(repaired_events[-2]["event_id"], result["repair_event_id"])
            self.assertEqual(repaired_events[-2]["event_type"], "tool_command")
            self.assertEqual(repaired_events[-2]["source_type"], "tool")
            self.assertEqual(repaired_events[-1]["event_id"], result["checkpoint_event_id"])
            self.assertEqual(repaired_events[-1]["event_type"], "checkpoint")

    def test_repair_stale_tail_refuses_content_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            events = _write_stale_tail_fixture(root, bac_file)
            events[2]["payload"]["summary"] = "Tampered after stale write"
            _rewrite_bac_events(bac_file, events)
            before = bac_file.read_bytes()

            result = repair_stale_tail(root, bac_file, apply=True)

            self.assertEqual(result["status"], "refused")
            self.assertIn("event_hash mismatch", result["reason"])
            self.assertEqual(bac_file.read_bytes(), before)

    def test_cli_repair_stale_tail_dry_run_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _cli_env()
            bac_file = root / "project.bac"
            _write_stale_tail_fixture(root, bac_file)
            before = bac_file.read_bytes()

            dry_run = _run_bac(root, "repair", "stale-tail", "--json", env=env)

            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            dry_output = json.loads(dry_run.stdout)
            self.assertEqual(dry_output["status"], "planned")
            self.assertEqual(bac_file.read_bytes(), before)

            applied = _run_bac(root, "repair", "stale-tail", "--json", "--apply", env=env)

            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_output = json.loads(applied.stdout)
            self.assertEqual(applied_output["status"], "repaired")
            self.assertEqual(verify_bac_file(bac_file).errors, [])

    def test_redaction_masks_secrets_and_records_metadata(self) -> None:
        redacted, metadata = redact_data({"command": "curl -H 'Authorization: sk-testsecret123456789012345'"})

        self.assertIn("[REDACTED]", redacted["command"])
        self.assertTrue(metadata)

    def test_verify_reports_non_object_event_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bac_file = Path(tmp) / "project.bac"
            genesis = build_genesis_event(Path(tmp))
            initialize_bac_file(bac_file, genesis)
            with ZipFile(bac_file, "w") as archive:
                archive.writestr(MANIFEST_PATH, canonical_json(_minimal_manifest(genesis)))
                archive.writestr(event_path(1), "[]")

            report = verify_bac_file(bac_file)
            self.assertEqual(report.status, "fail")
            self.assertTrue(any("event must be a JSON object" in error for error in report.errors))

    def test_verify_rejects_non_zip_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bac_file = Path(tmp) / "project.bac"
            bac_file.write_text("{}\n", encoding="utf-8")

            report = verify_bac_file(bac_file)
            self.assertEqual(report.status, "fail")
            self.assertTrue(any("valid v2 ZIP container" in error for error in report.errors))

    def test_verify_rejects_duplicate_event_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with ZipFile(bac_file, "a") as archive:
                    archive.writestr(event_path(1), canonical_json(genesis))

            report = verify_bac_file(bac_file)
            self.assertEqual(report.status, "fail")
            self.assertTrue(any("duplicate entry" in error for error in report.errors))

    def test_verify_rejects_event_sequence_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            record = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="ai_generation",
                source_type="ai",
                summary="Generated implementation outline",
            )
            with ZipFile(bac_file, "w") as archive:
                archive.writestr(MANIFEST_PATH, canonical_json(_minimal_manifest(genesis)))
                archive.writestr(event_path(1), canonical_json(genesis))
                archive.writestr(event_path(3), canonical_json(record))

            report = verify_bac_file(bac_file)
            self.assertEqual(report.status, "fail")
            self.assertTrue(any("contiguous" in error for error in report.errors))

    def test_verify_rejects_signed_trust_without_verified_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            signed = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="human_instruction",
                source_type="human",
                summary="Forged signed event",
            )
            signed["trust_level"] = "signed"
            signed = attach_event_hash(signed)
            append_event(bac_file, signed)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(any("signed trust_level requires a valid signature" in error for error in report.errors))

    def test_verify_rejects_anchored_trust_on_non_checkpoint_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            anchored = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="ai_generation",
                source_type="ai",
                summary="Forged anchored event",
            )
            anchored["trust_level"] = "anchored"
            anchored = attach_event_hash(anchored)
            append_event(bac_file, anchored)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(any("anchored trust_level is only valid on checkpoint events" in error for error in report.errors))

    def test_verify_rejects_anchored_checkpoint_without_valid_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            checkpoint = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="checkpoint",
                source_type="system",
                summary="Forged anchored checkpoint",
            )
            checkpoint["trust_level"] = "anchored"
            checkpoint = attach_event_hash(checkpoint)
            append_event(bac_file, checkpoint)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(any("anchored trust_level requires a valid remote anchor receipt" in error for error in report.errors))

    def test_verify_rejects_ai_generation_claimed_as_human(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            record = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="ai_generation",
                source_type="ai",
                summary="Generated implementation",
            )
            record["source_type"] = "human"
            record = attach_event_hash(record)
            append_event(bac_file, record)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(any("ai_generation must use source_type ai" in error for error in report.errors))

    def test_verify_rejects_human_approval_claimed_as_ai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            approval = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="human_approval",
                source_type="human",
                summary="Approved implementation",
            )
            approval["source_type"] = "ai"
            approval = attach_event_hash(approval)
            append_event(bac_file, approval)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(any("human_approval must use source_type human" in error for error in report.errors))

    def test_verify_rejects_session_started_claimed_as_human(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            session = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="session_started",
                source_type="system",
                summary="Started session",
            )
            session["source_type"] = "human"
            session = attach_event_hash(session)
            append_event(bac_file, session)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(any("session_started must use source_type system" in error for error in report.errors))

    def test_verify_rejects_file_snapshot_claimed_as_human(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            snapshot = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="file_snapshot",
                source_type="tool",
                summary="Captured file snapshot",
            )
            snapshot["source_type"] = "human"
            snapshot = attach_event_hash(snapshot)
            append_event(bac_file, snapshot)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(any("file_snapshot must use source_type tool" in error for error in report.errors))

    def test_verify_accepts_human_approval_of_previous_ai_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            ai_event = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="ai_generation",
                source_type="ai",
                summary="Generated implementation",
            )
            append_event(bac_file, ai_event)
            approval = build_record_event(
                root=root,
                prev_event_hash=ai_event["event_hash"],
                event_type="human_approval",
                source_type="human",
                summary="Approved AI-generated implementation",
                payload={
                    "approves_event_hash": ai_event["event_hash"],
                    "approval_scope": "accept_for_merge",
                },
            )
            append_event(bac_file, approval)
            checkpoint = build_record_event(
                root=root,
                prev_event_hash=approval["event_hash"],
                event_type="checkpoint",
                source_type="system",
                summary="Local checkpoint",
            )
            append_event(bac_file, checkpoint)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "warn")
            self.assertTrue(any("human contributions may be underrecorded" in warning for warning in report.warnings))

    def test_verify_rejects_human_approval_of_missing_event_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            approval = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="human_approval",
                source_type="human",
                summary="Approved missing event",
                payload={"approves_event_hash": "sha256:" + "f" * 64},
            )
            append_event(bac_file, approval)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(
                any("payload.approves_event_hash must reference a previous event_hash" in error for error in report.errors)
            )

    def test_verify_warns_when_human_file_change_claims_ai_generated_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            file_change = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="file_change",
                source_type="human",
                summary="AI-generated implementation was written to src/app.py",
            )
            append_event(bac_file, file_change)
            checkpoint = build_record_event(
                root=root,
                prev_event_hash=file_change["event_hash"],
                event_type="checkpoint",
                source_type="system",
                summary="Local checkpoint",
            )
            append_event(bac_file, checkpoint)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "warn")
            self.assertTrue(any("file_change/source_type=human" in warning for warning in report.warnings))

    def test_verify_warns_on_actor_kind_source_type_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            event = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="human_review",
                source_type="human",
                summary="Reviewed change",
                actor={"declared_name": "assistant", "declared_kind": "ai"},
            )
            append_event(bac_file, event)
            checkpoint = build_record_event(
                root=root,
                prev_event_hash=event["event_hash"],
                event_type="checkpoint",
                source_type="system",
                summary="Local checkpoint",
            )
            append_event(bac_file, checkpoint)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "warn")
            self.assertTrue(any("actor.declared_kind ai conflicts with source_type human" in warning for warning in report.warnings))

    def test_builder_rejects_contradictory_event_source_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            genesis = build_genesis_event(root)

            with self.assertRaisesRegex(ValueError, "ai_generation must use source_type ai"):
                build_record_event(
                    root=root,
                    prev_event_hash=genesis["event_hash"],
                    event_type="ai_generation",
                    source_type="human",
                    summary="Misattributed AI output",
                )

    def test_builder_accepts_tool_verification_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            verification = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="verification",
                source_type="tool",
                summary="Verified BAC ledger",
            )
            append_event(bac_file, verification)
            checkpoint = build_record_event(
                root=root,
                prev_event_hash=verification["event_hash"],
                event_type="checkpoint",
                source_type="system",
                summary="Local checkpoint",
            )
            append_event(bac_file, checkpoint)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "pass")

    def test_builder_rejects_unimplemented_signed_trust_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            genesis = build_genesis_event(root)

            with self.assertRaisesRegex(ValueError, "signed trust_level is not supported"):
                build_record_event(
                    root=root,
                    prev_event_hash=genesis["event_hash"],
                    event_type="human_instruction",
                    source_type="human",
                    summary="Claim a signature",
                    trust_level="signed",
                )

    def test_reading_rejects_oversized_bac_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bac_file = Path(tmp) / "project.bac"
            bac_file.write_text("not a zip", encoding="utf-8")

            with patch("bac.core.container.MAX_BAC_BYTES", 2):
                report = verify_bac_file(bac_file)
                self.assertEqual(report.status, "fail")
                self.assertTrue(any("exceeds maximum size" in error for error in report.errors))
                with self.assertRaisesRegex(ValueError, "exceeds maximum size"):
                    read_events(bac_file)

    def test_reading_rejects_too_many_event_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)

            with patch("bac.core.container.MAX_EVENT_COUNT", 0):
                report = verify_bac_file(bac_file)
                self.assertEqual(report.status, "fail")
                self.assertTrue(any("too many event members" in error for error in report.errors))
                with self.assertRaisesRegex(ValueError, "too many event members"):
                    read_events(bac_file)

    def test_reading_rejects_oversized_json_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)

            with patch("bac.core.container.MAX_MEMBER_UNCOMPRESSED_BYTES", 8):
                report = verify_bac_file(bac_file)
                self.assertEqual(report.status, "fail")
                self.assertTrue(any("uncompressed size exceeds limit" in error for error in report.errors))
                with self.assertRaisesRegex(ValueError, "uncompressed size exceeds limit"):
                    read_events(bac_file)

    def test_timeline_filters_human_contributions_by_date(self) -> None:
        events = [
            _timeline_event("2026-05-30T23:59:59Z", "human_instruction", "human", "Previous request"),
            _timeline_event("2026-05-31T00:00:00Z", "human_instruction", "human", "Morning request"),
            _timeline_event("2026-05-31T12:00:00Z", "ai_generation", "ai", "AI implementation"),
            _timeline_event("2026-05-31T23:59:59Z", "human_review", "human", "Review feedback"),
            _timeline_event("2026-06-01T00:00:00Z", "human_approval", "human", "Next day approval"),
        ]

        items = timeline(events, source_type="human", on="2026-05-31")

        self.assertEqual([item["summary"] for item in items], ["Morning request", "Review feedback"])

    def test_timeline_filters_source_and_time_range_before_limit(self) -> None:
        events = [
            _timeline_event("2026-05-30T10:00:00Z", "human_instruction", "human", "First"),
            _timeline_event("2026-05-31T10:00:00Z", "human_review", "human", "Second"),
            _timeline_event("2026-06-01T10:00:00Z", "human_approval", "human", "Third"),
            _timeline_event("2026-06-01T11:00:00Z", "ai_generation", "ai", "AI item"),
        ]

        items = timeline(events, limit=1, source_type="human", since="2026-05-31", until="2026-06-01")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["summary"], "Third")

    def test_timeline_rejects_conflicting_date_filters(self) -> None:
        with self.assertRaisesRegex(ValueError, "--on cannot be combined"):
            timeline([], on="2026-05-31", since="2026-05-01")

    def test_cli_records_human_input_without_full_prompt_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _cli_env()
            message_file = root / "user-message.txt"
            message_text = "请优化 BAC 记录。token=super-secret-token\n不要保存完整 prompt。"
            message_file.write_text(message_text, encoding="utf-8")

            self.assertEqual(_run_bac(root, "init", "--mode", "local", "--json", env=env).returncode, 0)
            first = _run_bac(
                root,
                "input",
                "record",
                "--channel",
                "ai_tool_user_message",
                "--host",
                "codex",
                "--session-id",
                "s1",
                "--message-index",
                "1",
                "--message-file",
                str(message_file),
                "--json",
                env=env,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            output = json.loads(first.stdout)
            self.assertEqual(output["status"], "recorded")
            self.assertEqual(output["skipped"], 0)

            events = read_events(root / "project.bac")
            self.assertEqual(len(events), 2)
            event = events[-1]
            self.assertEqual(event["source_type"], "human")
            self.assertEqual(event["event_type"], "human_instruction")
            provenance = event["payload"]["input_provenance"]
            self.assertEqual(provenance["channel"], "ai_tool_user_message")
            self.assertEqual(provenance["host"], "codex")
            self.assertEqual(provenance["session_id"], "s1")
            self.assertEqual(provenance["message_index"], 1)
            self.assertTrue(provenance["message_hash"].startswith("sha256:"))
            self.assertFalse(provenance["recorded_full_text"])
            self.assertNotIn(message_text, json.dumps(event, ensure_ascii=False))
            self.assertTrue(any(item.get("type") == "human_input_message" for item in event["evidence"]))

            second = _run_bac(
                root,
                "input",
                "record",
                "--channel",
                "ai_tool_user_message",
                "--host",
                "codex",
                "--session-id",
                "s1",
                "--message-index",
                "1",
                "--message-file",
                str(message_file),
                "--json",
                env=env,
            )

            self.assertEqual(second.returncode, 0, second.stderr)
            duplicate = json.loads(second.stdout)
            self.assertEqual(duplicate["status"], "skipped")
            self.assertEqual(duplicate["skipped"], 1)
            self.assertEqual(len(read_events(root / "project.bac")), 2)

    def test_cli_imports_prompt_log_as_supplemental_human_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _cli_env()
            prompt_log = root / "Prompts.md"
            prompt_log.write_text(
                "\n".join(
                    [
                        "# Prompts",
                        "",
                        "请先审查实现是否正确，api_key=abc123secret。",
                        "---",
                        "```",
                        "批准发布 release v1.2.3",
                        "```",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(_run_bac(root, "init", "--mode", "local", "--json", env=env).returncode, 0)
            result = _run_bac(root, "input", "import-log", "--source-file", "Prompts.md", "--json", env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "imported")
            self.assertEqual(output["recorded"], 2)
            self.assertEqual(output["skipped"], 0)
            events = read_events(root / "project.bac")
            self.assertEqual(len(events), 3)
            imported = events[1:]
            self.assertTrue(all(event["source_type"] == "human" for event in imported))
            self.assertTrue(all(event["payload"]["input_provenance"]["channel"] == "prompt_log" for event in imported))
            self.assertTrue(all("source_path" in event["payload"]["input_provenance"] for event in imported))
            self.assertNotIn("abc123secret", json.dumps(imported, ensure_ascii=False))

            duplicate = _run_bac(root, "input", "import-log", "--source-file", "Prompts.md", "--json", env=env)

            self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
            duplicate_output = json.loads(duplicate.stdout)
            self.assertEqual(duplicate_output["status"], "skipped")
            self.assertEqual(duplicate_output["recorded"], 0)
            self.assertEqual(duplicate_output["skipped"], 2)
            self.assertEqual(len(read_events(root / "project.bac")), 3)

    def test_inspect_human_includes_input_provenance_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _cli_env()
            message_file = root / "user-message.txt"
            message_file.write_text("请 review 这次修改可以吗？", encoding="utf-8")

            self.assertEqual(_run_bac(root, "init", "--mode", "local", "--json", env=env).returncode, 0)
            self.assertEqual(
                _run_bac(
                    root,
                    "input",
                    "record",
                    "--host",
                    "codex",
                    "--session-id",
                    "s1",
                    "--message-index",
                    "7",
                    "--message-file",
                    str(message_file),
                    "--json",
                    env=env,
                ).returncode,
                0,
            )

            result = _run_bac(root, "inspect", "--human", "--json", env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            items = json.loads(result.stdout)
            self.assertEqual(items[0]["event_type"], "human_review")
            self.assertEqual(items[0]["input_provenance"]["channel"], "ai_tool_user_message")
            self.assertEqual(items[0]["input_provenance"]["host"], "codex")
            self.assertEqual(items[0]["input_provenance"]["classification"], "review")

    def test_verify_validates_human_input_provenance_and_warns_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            human_input = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="human_instruction",
                source_type="human",
                summary="Recorded input",
                payload={
                    "input_provenance": {
                        "format": "bac.human_input.v1",
                        "channel": "ai_tool_user_message",
                        "host": "codex",
                        "session_id": "s1",
                        "message_index": 1,
                        "message_hash": "sha256:" + "1" * 64,
                        "recorded_full_text": False,
                        "classification": "instruction",
                    }
                },
                evidence=[
                    {
                        "type": "human_input_message",
                        "message_hash": "sha256:" + "1" * 64,
                        "redacted": True,
                        "excerpt": "Recorded input",
                    }
                ],
            )
            append_event(bac_file, human_input)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "warn")
            self.assertFalse(any("input_provenance" in error for error in report.errors))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            ai_event = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="ai_generation",
                source_type="ai",
                summary="Generated code",
            )
            append_event(bac_file, ai_event)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "warn")
            self.assertTrue(any("human contributions may be underrecorded" in warning for warning in report.warnings))

    def test_verify_rejects_malformed_human_input_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bac_file = root / "project.bac"
            genesis = build_genesis_event(root)
            initialize_bac_file(bac_file, genesis)
            event = build_record_event(
                root=root,
                prev_event_hash=genesis["event_hash"],
                event_type="human_instruction",
                source_type="human",
                summary="Malformed input",
                payload={
                    "input_provenance": {
                        "format": "wrong",
                        "channel": "",
                        "message_hash": "not-a-hash",
                        "recorded_full_text": "false",
                    }
                },
                evidence=[],
            )
            append_event(bac_file, event)

            report = verify_bac_file(bac_file)

            self.assertEqual(report.status, "fail")
            self.assertTrue(any("input_provenance.format must be bac.human_input.v1" in error for error in report.errors))
            self.assertTrue(any("input_provenance.message_hash must be sha256:<hex>" in error for error in report.errors))

    def test_cli_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            init_result = subprocess.run(
                [sys.executable, "-m", "bac", "--root", str(root), "init", "--json"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            invalid_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bac",
                    "--root",
                    str(root),
                    "record",
                    "--event-type",
                    "ai_generation",
                    "--source-type",
                    "human",
                    "--summary",
                    "Misattributed AI output",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(invalid_result.returncode, 2)
            self.assertIn("ai_generation must use source_type ai", invalid_result.stderr)
            self.assertIn("human_approval/source_type=human", invalid_result.stderr)
            self.assertEqual(len(read_events(root / "project.bac")), 1)

            invalid_approval_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bac",
                    "--root",
                    str(root),
                    "record",
                    "--event-type",
                    "human_approval",
                    "--source-type",
                    "human",
                    "--summary",
                    "Approve missing event",
                    "--payload-json",
                    json.dumps({"approves_event_hash": "sha256:" + "f" * 64}),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(invalid_approval_result.returncode, 2)
            self.assertIn("payload.approves_event_hash must reference an earlier event_hash", invalid_approval_result.stderr)
            self.assertEqual(len(read_events(root / "project.bac")), 1)

            record_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bac",
                    "--root",
                    str(root),
                    "record",
                    "--event-type",
                    "checkpoint",
                    "--source-type",
                    "system",
                    "--summary",
                    "Checkpoint current head",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(record_result.returncode, 0, record_result.stderr)

            human_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bac",
                    "--root",
                    str(root),
                    "record",
                    "--event-type",
                    "human_instruction",
                    "--source-type",
                    "human",
                    "--summary",
                    "Human requirement",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(human_result.returncode, 0, human_result.stderr)

            inspect_result = subprocess.run(
                [sys.executable, "-m", "bac", "--root", str(root), "inspect", "--human", "--json"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(inspect_result.returncode, 0, inspect_result.stderr)
            human_items = json.loads(inspect_result.stdout)
            self.assertEqual(len(human_items), 1)
            self.assertEqual(human_items[0]["summary"], "Human requirement")
            self.assertEqual(human_items[0]["source_type"], "human")

            verify_result = subprocess.run(
                [sys.executable, "-m", "bac", "--root", str(root), "verify", "--json"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(verify_result.returncode, 0, verify_result.stderr)
            self.assertEqual(json.loads(verify_result.stdout)["status"], "pass")


class HashAttachTests(unittest.TestCase):
    def test_attach_event_hash_sets_computed_hash(self) -> None:
        event = {
            "format": FORMAT_VERSION,
            "event_id": "example",
            "event_type": "genesis",
            "source_type": "system",
            "trust_level": "observed",
            "created_at": "2026-05-26T10:30:00Z",
            "project": {
                "root_path": "/tmp/project",
                "root_hash": "sha256:" + "0" * 64,
                "git_remote": None,
                "git_commit": None,
                "git_branch": None,
                "worktree_dirty": False,
            },
            "actor": {},
            "payload": {},
            "evidence": [],
            "redactions": [],
            "prev_event_hash": None,
            "event_hash": None,
            "signature": None,
        }
        with_hash = attach_event_hash(event)
        self.assertEqual(with_hash["event_hash"], compute_event_hash(with_hash))


def _minimal_manifest(genesis: dict) -> dict:
    return {
        "format": "bac.container.v2",
        "event_format": FORMAT_VERSION,
        "project": genesis["project"],
        "genesis_event_hash": genesis["event_hash"],
        "storage": {"kind": "zip", "event_path_template": EVENT_PATH_TEMPLATE},
    }


def _timeline_event(created_at: str, event_type: str, source_type: str, summary: str) -> dict:
    return {
        "created_at": created_at,
        "event_type": event_type,
        "source_type": source_type,
        "trust_level": "declared",
        "payload": {"summary": summary},
        "event_hash": "sha256:" + "0" * 64,
    }


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return env


def _run_bac(root: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bac", "--root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _write_stale_tail_fixture(root: Path, bac_file: Path) -> list[dict[str, Any]]:
    genesis = build_genesis_event(root)
    initialize_bac_file(bac_file, genesis)
    first = build_record_event(
        root=root,
        prev_event_hash=genesis["event_hash"],
        event_type="ai_generation",
        source_type="ai",
        summary="Generated implementation",
    )
    stale = build_record_event(
        root=root,
        prev_event_hash=genesis["event_hash"],
        event_type="test_result",
        source_type="tool",
        summary="Verified implementation from stale head",
    )
    append_event(bac_file, first)
    events = [genesis, first, stale]
    _rewrite_bac_events(bac_file, events)
    report = verify_bac_file(bac_file)
    assert report.status == "fail"
    assert any("prev_event_hash does not match previous event_hash" in error for error in report.errors)
    return events


def _rewrite_bac_events(bac_file: Path, events: list[dict[str, Any]]) -> None:
    with ZipFile(bac_file, "r") as archive:
        manifest = archive.read(MANIFEST_PATH)
    with ZipFile(bac_file, "w") as archive:
        archive.writestr(MANIFEST_PATH, manifest)
        for index, event in enumerate(events, start=1):
            archive.writestr(event_path(index), canonical_json(event))


if __name__ == "__main__":
    unittest.main()
