<div align="center">

# 🧭 Bensz Auto Contribution

**A tamper-evident contribution ledger for human–AI software collaboration**

[![Release](https://img.shields.io/github/v/tag/huangwb8/bensz-auto-contribution?label=release&color=blue)](https://github.com/huangwb8/bensz-auto-contribution/tags)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![BAC Format](https://img.shields.io/badge/BAC_format-v2-7C3AED.svg)](docs/bac-tutorial.md)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | [中文](README.zh-CN.md)

</div>

---

## What is BAC?

When a project is built with an AI coding tool, the final diff shows *what* changed, but usually not *how* it came to be. A requirement may have come from a person, an implementation from an AI, and a test result from a tool. If all of that is reduced to “the author changed these lines”, the useful context is gone.

**Bensz Auto Contribution (BAC)** is a small command-line tool that keeps that context in a project-bound `.bac` file. Think of it as a flight recorder for development: it records the sequence of human intent, AI work, tool observations, file evidence, and approvals so a team can review the journey later.

BAC is an audit aid, not a judge. It does not decide academic authorship, legal ownership, or who is ultimately responsible. It makes the process easier to explain and makes later tampering easier to notice.

### The idea in one minute

```text
human request → AI proposal/code → file change → tool/test evidence → human approval
```

Each arrow becomes an append-only event. Events point to the previous event with a hash, so changing, removing, or reordering history breaks the chain. The record distinguishes four direct sources:

| Source | What it means | Typical examples |
| --- | --- | --- |
| `human` | A person supplied intent or made a decision | requirement, constraint, review, hand-written edit, approval |
| `ai` | An AI generated or proposed work | plan, code draft, refactor, repair |
| `tool` | A program produced an observation | command output, test result, diff summary |
| `system` | BAC recorded a bookkeeping event | genesis, checkpoint, verification |

The source is deliberately about **where an event came from**, not a shortcut for assigning all credit to one party.

## The principles behind it

- **Record the process, not a retrospective story.** Capture intent and evidence near the time they occur.
- **Approval is not authorship.** If a person accepts AI-generated code, keep the AI generation event and add a separate human approval event.
- **Evidence over assertion.** File hashes, diffs, command exit codes, and test reports are more useful than an unchecked “done” label.
- **Privacy by default.** Human input is represented by a redacted summary and a domain-separated hash; full private prompts and secrets are not written by default.
- **Be honest about security.** BAC is *tamper-evident*, not tamper-proof. A local hash chain cannot stop someone with write access from rewriting history; it can make the rewrite detectable. Optional signed remote receipts help with tail-truncation checks.
- **Keep the human in the loop.** The ledger supports review and dispute reconstruction; it does not replace project policy or human judgment.

## Where it fits

BAC is useful anywhere a person and an AI jointly produce an artifact:

- **Software:** connect a requirement to generated code, file changes, and test evidence.
- **Research and writing:** keep drafts, human revisions, tool checks, and approvals distinguishable.
- **Team work:** reconstruct who decided what, which parts were proposed by AI, and which results were actually observed.

The same rule applies in every setting: record the source and evidence of each step instead of guessing ownership from the final document.

## A five-minute quick start

### Requirements and installation

- Python 3.10+
- No runtime third-party dependencies

```bash
python -m pip install bensz-auto-contribution
# or, from a checkout:
python -m pip install -e .
```

### Start a ledger and record a small session

Run these commands from the project root. The default ledger is `docs/contribution.bac`; `bac init` creates `docs/` when needed.

```bash
# 1. Start a project-bound .bac container
bac init

# 2. Capture the human requirement (the AI host should do this on receipt)
bac input record \
  --host codex \
  --session-id s1 \
  --message-index 1 \
  --message-file /tmp/user-message.txt

# 3. Record AI work and a tool observation as separate events
bac record --event-type ai_generation --source-type ai \
  --summary "Implemented hash-chain verifier"
bac record --event-type test_result --source-type tool \
  --summary "Unit tests passed" \
  --command-text "python -m pytest -q" --exit-code 0

# 4. Verify and inspect the timeline
bac verify
bac inspect
```

For a host integration, call `bac input record` when the user message arrives. It stores a low-sensitivity provenance record rather than the complete prompt. A `Prompts.md` import is available for backfill, but it is supplementary evidence, not the primary source.

All commands accept `--root` (target project) and `--bac-file` (custom ledger path). `init`, `record`, `input`, `verify`, `repair`, and `inspect` also support `--json` for AI tools and automation.

## What the `.bac` file contains

The default file is one ZIP-based v2 container:

```text
docs/contribution.bac
├── manifest.json
└── events/
    ├── 000000000001.json
    └── 000000000002.json
```

`manifest.json` binds the ledger to the project and records storage conventions. Each event is canonical JSON and includes:

- event type, direct source type, trust level, and timestamp;
- project context (root binding, git commit/branch and dirty state);
- a payload such as a summary, command, or file snapshot;
- evidence such as file hashes, diff summaries, exit codes, or test results;
- `prev_event_hash` and `event_hash`, which form the verifiable chain.

The verifier checks the ZIP structure, duplicate paths, continuous event numbering, manifest/genesis consistency, event semantics, and the recomputed hash chain. See [BAC Tutorial](docs/bac-tutorial.md) for a field-by-field walkthrough.

## Common workflows

### Human input and contribution review

```bash
bac inspect --human
bac inspect --source-type human --since 2026-05-01 --until 2026-05-31 --json
```

Date-only boundaries use UTC calendar dates; pass an ISO-8601 timestamp for an exact boundary. A human accepting AI work should add a `human_approval` event that points to the earlier AI event. BAC intentionally does not turn that approval into a claim that the human wrote the code.

### Checkpoints, repair, and concurrent writers

```bash
bac record --event-type checkpoint --source-type system --summary "Local checkpoint"
bac repair stale-tail --json              # dry run
bac repair stale-tail --json --apply      # apply only after review
```

Routine writes use a per-ledger OS lock, rebuild a temporary container, verify it, and atomically replace the ledger. This protects normal concurrent `bac record` calls. `repair stale-tail` is deliberately narrow: it can rebase only a mechanically stale tail and refuses content/attribution changes, signed or anchored events, checkpoints, and non-tail breaks.

### Optional private anchors

`bac init` defaults to local-first `hybrid` mode. A request sends a blinded `anchor_hash` (and, when configured, a low-sensitivity client summary), not `.bac` contents, paths, diffs, prompts, actors, project names, or the raw head hash.

```bash
bac anchor request --json
bac anchor import --receipt-file receipt.json --public-key "$ANCHOR_PUBLIC_KEY"
bac verify --require-anchor
```

For a self-hosted reference service:

```bash
docker compose -f server/docker-compose.yml up --build
```

The `bac cloud register/login/link/status` commands provide an optional user-facing flow. Tokens stay in the local user configuration and are never written to `.bac`; production anchor writes require a token and safe HTTPS configuration.

## Security and limits

BAC can reveal edited event content, missing or reordered events, duplicate ZIP members, broken links, inconsistent checkpoints, and common source-laundering patterns. It cannot prove that every real-world action was recorded, prevent an authorized user from rewriting a ledger, or settle authorship and liability questions on its own. A remote signed receipt proves that a blinded head existed at a service timestamp; it does not prove the completeness of the history.

The reader should therefore treat a `.bac` file as **verifiable evidence with stated limits**, not as an infallible truth source.

## Questions new users ask

**Do I need to record every keystroke?** No. Record meaningful transitions—intent, AI work, file evidence, tool results, and approvals. A host integration can automate the first input event.

**Can BAC reconstruct work that happened before initialization?** Only from evidence you still have. Prompt logs and imported notes can help with backfill, but retrospective records are naturally weaker than records captured in the moment.

**Is a `.bac` file an authorship or legal certificate?** No. It is a verifiable process record. Institutions, journals, contracts, and people still decide authorship and responsibility.

**What if verification fails?** Read the report first. If the problem is a mechanically stale tail, `bac repair stale-tail` can produce a constrained dry-run plan; it will refuse content or attribution rewrites.

## Development

```bash
python -m pytest -q
python -m unittest discover -s tests -v
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

Tests cover canonicalization, v2 containers, hash chains, tamper detection, redaction, checkpoints, private-anchor receipts, server flows, and CLI end-to-end behavior. See [PyPI Release](docs/pypi-release.md) and [DockerHub Release](docs/dockerhub-release.md) for publishing workflows.

## Repository map

```text
src/bac/       CLI, event model, storage, verification, anchors, reports
tests/         client and end-to-end tests
server/        optional reference anchor server
docs/          BAC tutorial, release guides, and plans
```

## Contributing

Issues and pull requests are welcome around the `.bac` format, threat model, AI-tool adapters, verification, signing/timestamping, and developer experience. When changing attribution logic, keep the boundary precise: BAC records a process and its evidence; it does not declare an immutable owner.

## License

MIT License
