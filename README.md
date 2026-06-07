<div align="center">

# 🧭 Bensz Auto Contribution

**Tamper-evident contribution attribution for human-AI software collaboration**

[![Release](https://img.shields.io/github/v/tag/huangwb8/bensz-auto-contribution?label=release&color=blue)](https://github.com/huangwb8/bensz-auto-contribution/tags)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![BAC Format](https://img.shields.io/badge/BAC_format-v2-7C3AED.svg)](docs/bac-tutorial.md)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | [中文](README.zh-CN.md)

</div>

---

## ✨ Introduction

Bensz Auto Contribution, or **BAC**, is a contribution attribution and audit system designed for AI coding tools. Its core artifact is a `.bac` file: a project-bound, append-only, tamper-evident record of what came from humans, what came from AI, what came from tools, and what evidence was observed during development.

BAC does not claim that a file can never be modified. Instead, it makes changes detectable through structured events, canonical JSON, hash chaining, local checkpoints, project context binding, and future-ready signature and timestamp fields.

**🌟 Core Highlight**: BAC gives AI coding sessions a durable audit trail. It helps teams explain AI usage, review collaboration boundaries, verify generated work, and reconstruct development history without mixing human intent, AI generation, tool output, and file evidence into one vague blob.

### Key Features

- 🧑‍💻 **Human-AI Attribution**: Explicitly separates `human`, `ai`, `tool`, and `system` sources.
- 🧾 **Append-Only Event Model**: Records contribution history as ordered events instead of overwriting prior state.
- 🔗 **Hash-Chain Verification**: Detects modified, inserted, deleted, duplicated, or reordered events.
- 📦 **Single-File `.bac` Container**: Stores a ZIP-based v2 ledger with `manifest.json` and canonical JSON event files.
- 🛡️ **Tamper-Evident Security Boundary**: Describes integrity guarantees honestly without overstating immutability.
- ⏱️ **Private Anchors**: Supports local mode and hybrid mode with blinded remote anchor receipts.
- 🧠 **AI Tool Ready**: Designed for Codex CLI, Claude Code, and other agentic coding environments.
- 🔍 **Evidence-Aware Records**: Captures file hashes, git diff summaries, command text, exit codes, test results, and checkpoints.
- 🧼 **Sensitive Data Redaction**: Avoids storing secrets, private prompts, or unrelated user data by default.

---

<div align="center">

### ⭐ If this project helps you, please give it a Star!

Building reliable attribution for AI-assisted work takes careful design, testing, and threat modeling. Your Star helps more builders discover BAC.

[![Star History Chart](https://api.star-history.com/svg?repos=huangwb8/bensz-auto-contribution&type=Date)](https://star-history.com/#huangwb8/bensz-auto-contribution&Date)

</div>

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- No runtime third-party dependencies

### Installation

```bash
python -m pip install bensz-auto-contribution

# source or development install
python -m pip install -e .
```

### Basic Usage

Create a single-file `.bac` container and write the genesis event:

```bash
bac init
```

Record a human requirement:

```bash
bac record \
  --event-type human_instruction \
  --source-type human \
  --summary "Add BAC verification workflow"
```

AI tool hosts should record the user message as soon as it is received. This is the primary path for capturing human intent without storing the full private prompt:

```bash
bac input record \
  --host codex \
  --session-id s1 \
  --message-index 1 \
  --message-file /tmp/user-message.txt
```

Prompt logs such as `Prompts.md` are only supplemental evidence for backfill or cross-checking:

```bash
bac input import-log --source-file Prompts.md
```

Record AI generation or implementation intent:

```bash
bac record \
  --event-type ai_generation \
  --source-type ai \
  --summary "Implemented hash-chain verifier"
```

Record a tool result:

```bash
bac record \
  --event-type test_result \
  --source-type tool \
  --summary "Unit tests passed" \
  --command-text "python -m unittest discover -s tests -v" \
  --exit-code 0
```

Record a local checkpoint to reduce tail-truncation risk:

```bash
bac record \
  --event-type checkpoint \
  --source-type system \
  --summary "Local checkpoint"
```

Verify integrity:

```bash
bac verify
```

Plan a narrow repair for a mechanically stale ledger tail. This is a dry run and does not write the `.bac` file:

```bash
bac repair stale-tail --json
```

Routine CLI writes are serialized with a per-ledger OS lock stored outside the project directory, so two concurrent `bac record` calls do not append from the same stale head. Each append rebuilds a same-directory temporary ZIP container, verifies it, and atomically replaces the ledger instead of mutating the ZIP tail in place. `repair stale-tail` is mainly for historical ledgers, external integrations, or abnormal merges that already left a broken tail.

Apply the repair only after reviewing the plan:

```bash
bac repair stale-tail --json --apply
```

Inspect the contribution timeline:

```bash
bac inspect
```

Extract human contributions, optionally by date:

```bash
bac inspect --human
bac inspect --human --on 2026-05-31
bac inspect --source-type human --since 2026-05-01 --until 2026-05-31 --json
```

Date-only `--since`, `--until`, and `--on` values are interpreted as UTC calendar dates. `--until 2026-05-31` includes events through the end of that UTC date; pass an ISO-8601 timestamp for an exact boundary.

All commands support `--root` for the target project root and `--bac-file` for a custom `.bac` path. `init`, `record`, `input`, `verify`, `repair`, and `inspect` also support `--json` for machine-readable output.

### Private Anchor Workflow

`bac init` defaults to `hybrid` mode while keeping records local-first. It can create a blinded anchor request without uploading `.bac` content, file paths, diffs, prompts, actors, project names, or the raw `head_hash`:

```bash
bac anchor request --json
```

Import a signed receipt from an anchor service:

```bash
bac anchor import --receipt-file receipt.json --public-key "$ANCHOR_PUBLIC_KEY"
bac verify --require-anchor
```

For a configured service:

```bash
bac config set anchor.url http://localhost:8080
bac anchor push --allow-insecure-anchor-url
```

`bac anchor push` allows only safe `https://` public URLs by default, including DNS resolution checks for private and local addresses. The explicit insecure flag is intended for local development only. For production anchor servers that require a write token, pass `--token` or set `BAC_ANCHOR_API_TOKEN`; BAC does not write that token into `.bac`.

For a user-facing BAC Cloud flow, users can register or log in, store a local token outside `.bac`, and bind a project ledger:

```bash
bac cloud register --url https://bac.example.com --email user@example.com
bac cloud login --url https://bac.example.com --email user@example.com
bac cloud link --url https://bac.example.com --ledger-name my-project
bac cloud status
```

`bac cloud link` creates a server-side ledger, enables `anchor.require` plus automatic anchoring, and immediately anchors the newly linked ledger head. Future `bac record` calls still write the local `.bac` file, then submit a blinded `anchor_hash` and low-sensitivity `client_summary` to the configured service. The service returns a signed receipt that is appended locally as an anchored checkpoint.

The optional reference service lives in `server/` and can be started with:

```bash
docker compose -f server/docker-compose.yml up --build
```

## 🧩 Where BAC Fits

BAC is a process record and audit aid, not a final judge of contribution ownership.

In AI-assisted research, writing, and software projects, BAC can record human requirements, constraints, reviews, hand-written edits, final approvals, AI drafts, refactoring proposals, generated code, command outputs, tests, citation checks, build logs, file snapshots, and diff summaries.

For AI coding sessions, the most reliable human-input source is the user message submitted to the AI tool host. `bac input record` writes a `source_type=human` event with a summary, redacted excerpt, provenance channel, optional host/session/index, and a domain-separated message hash. It does not store the full prompt by default. A message hash is useful audit evidence, but it is not a zero-leakage privacy guarantee for short or guessable prompts.

If a user pastes logs, web text, generated code, or other third-party material into a prompt, BAC records that the human submitted that context. It does not automatically claim that every pasted fragment was authored by the human.

Approval is not authorship. If a human accepts AI-generated work, BAC should record the AI work as `ai_generation/source_type=ai` and then append a separate `human_approval/source_type=human` event. Rewriting AI generation or AI-driven file changes as human creation is treated as contribution source laundering.

These records can support AI usage disclosure, internal review, compliance notes, and dispute reconstruction. They do not automatically determine academic authorship, legal ownership, or final responsibility. Those decisions still require project policy, institutional rules, journal guidelines, and human judgment.

## 📦 `.bac` Format

The default file is `docs/contribution.bac`. If `docs/` does not exist, `bac init` creates it under the project root. Externally, it is one file. Internally, it is a ZIP container with at least:

```text
manifest.json
events/000000000001.json
events/000000000002.json
```

`manifest.json` records the container version, event format, project binding information, genesis event hash, and storage conventions. Each file under `events/` is one canonical JSON event. Event filenames are continuous and start at `000000000001.json`.

A BAC event includes:

- `format`: currently `bac.event.v2`
- `event_type`: examples include `genesis`, `human_instruction`, `ai_generation`, `tool_command`, `file_change`, `test_result`, and `checkpoint`
- `source_type`: one of `human`, `ai`, `tool`, or `system`; it records the direct source of this event, not a preferred credit label
- `trust_level`: one of `declared`, `observed`, `signed`, `verified`, or `anchored`; `signed` is reserved until event signatures are implemented, and `anchored` is valid only for checkpoint events with a verified remote receipt
- `project`: root path, project binding hash, git remote, commit, branch, and dirty state
- `payload`: summary, command data, file snapshots, or event-specific content
- `evidence`: diff summaries, file hashes, command results, or other verifiable evidence
- `redactions`: fields removed or masked for safety
- `prev_event_hash` and `event_hash`: the verifiable hash chain

Human approval of earlier AI or tool work can be linked in payload:

```json
{
  "event_type": "human_approval",
  "source_type": "human",
  "payload": {
    "summary": "Human approved AI-generated implementation",
    "approves_event_hash": "sha256:<previous-ai-event-hash>",
    "approval_scope": "accept_for_merge"
  }
}
```

The verifier checks whether the file is a valid ZIP container, whether internal paths are duplicated, whether event numbering is continuous, whether the manifest matches the genesis event, and whether the hash chain can be recomputed.

For a field-by-field walkthrough, see [BAC Tutorial](docs/bac-tutorial.md).

## 🛡️ Security Model

BAC is **tamper-evident**, not tamper-proof.

It can detect common integrity problems such as edited event content, missing events, reordered events, duplicated internal ZIP paths, broken event numbering, mismatched genesis metadata, invalid hash links, and checkpoint inconsistencies.

It also checks attribution semantics for common source laundering attacks. For example, `ai_generation` must use `source_type=ai`, `human_approval` must use `source_type=human`, `tool_command` and `test_result` must use `source_type=tool`, and system events such as `genesis`, `checkpoint`, and `verification` must use `source_type=system`. A `human_approval.payload.approves_event_hash` value must point to an earlier event in the same ledger.

For human input events, `bac verify` checks the `payload.input_provenance` and matching redacted evidence. If a ledger has AI activity but no human input provenance, verification returns a warning because human contributions may be underrecorded.

Without an external anchor, a purely local hash chain cannot fully prevent tail truncation. BAC therefore supports local checkpoints and remote signed receipts. A valid receipt proves that a blinded ledger head existed at the service timestamp; it does not prove that every real-world action was recorded.

Routine writes use a per-ledger OS lock and atomic container replacement: writers wait for the ledger lock, read the latest head inside the critical section, write a verified temporary ZIP next to the target ledger, and replace the old ledger only after the new container verifies. The lock is keyed by the resolved `.bac` path and kept outside the project directory, so normal writes do not leave `.bac.lock` sidecar files next to the ledger. If an older BAC version already left a project-side lock file, it can be deleted after confirming no BAC command is running. This keeps normal multi-agent writes serialized and prevents a failed append from partially mutating the existing ZIP central directory.

`bac repair stale-tail` is an explicit, constrained maintenance command for historical ledgers that already contain a mechanical stale-head tail caused by an old head, concurrent append, or git rollback/merge. It only changes tail `prev_event_hash` values and derived `event_hash` values, refuses content or attribution changes, refuses signed/anchored/checkpointed tail events, defaults to dry-run, and appends a tool repair record plus a local checkpoint when applied.

The verifier treats `.bac` files as untrusted input: it bounds container size, event count, and JSON member size before reading. The reference anchor server is open for local development, but production mode requires bearer tokens for anchor writes, admin access, and ledger receipt queries.

## 🧪 Development

Run the test suite:

```bash
python -m pytest -q
python -m unittest discover -s tests -v
```

Current coverage includes canonicalization, v2 container structure, hash-chain recomputation, tamper detection, duplicate internal path detection, checkpoint verification, private anchor receipt verification, sensitive data redaction, server API flows, and CLI end-to-end flows.

Build and check PyPI distributions locally:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

Releases are published to PyPI through GitHub Actions and PyPI Trusted Publishing. See [PyPI Release](docs/pypi-release.md).

Publish the BAC Anchor Server Docker image directly to DockerHub as `linux/amd64`:

```bash
make dockerhub-publish
```

See [DockerHub Release](docs/dockerhub-release.md). This path is intentionally local/publisher-machine based and does not use GitHub Actions.

## 🗂️ Project Structure

```text
bensz-auto-contribution/
├── AGENTS.md
├── CHANGELOG.md
├── CLAUDE.md
├── LICENSE
├── README.md
├── README.zh-CN.md
├── docs
│   ├── bac-tutorial.md
│   ├── dockerhub-release.md
│   ├── pypi-release.md
│   └── plans
├── Makefile
├── pyproject.toml
├── src
│   └── bac
│       ├── adapters
│       ├── core
│       ├── report
│       ├── service
│       └── storage
├── tests
├── tools
└── server
```

## 🤖 AI-Assisted Development

This repository includes project instructions for AI coding tools:

- `AGENTS.md` for OpenAI Codex CLI
- `CLAUDE.md` for Claude Code

When changing contribution attribution logic, keep the security boundary precise: BAC provides verifiable, tamper-evident records. It should not be described as impossible to modify.

## 🤝 Contributing

Issues and pull requests are welcome around the `.bac` file format, threat model, AI tool integration, verification logic, signing and timestamping, and developer experience.

## 📄 License

MIT License
