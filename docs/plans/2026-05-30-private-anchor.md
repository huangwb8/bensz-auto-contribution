# Private Anchor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 BAC 增加隐私保护型外部锚定能力，使创作者无法在锚定时间之后单方面重写一套无痕历史，同时避免上传 `.bac` 内容、文件路径、项目名、diff 或提示词。

**Architecture:** 最推荐方式是 `private-anchor`：本地 `.bac` 继续保存完整事件链，客户端只把盲化后的 `anchor_hash` 发给外部锚定服务，服务返回带服务端时间戳和 Ed25519 签名的 receipt。本地用 `checkpoint` 事件保存 receipt，验证器可离线校验 receipt 签名，并检查 receipt 是否锚定了该 checkpoint 之前的 `head_hash`。

**Tech Stack:** Python 3.10+，现有 ZIP v2 BAC 容器，canonical JSON，SHA-256；新增可选 `cryptography` extra 用于 Ed25519 签名验证；测试继续使用 `unittest` 与 CLI 子进程端到端测试。

**Minimal Change Scope:** 允许修改 `src/bac/core/`、`src/bac/service/`、`src/bac/adapters/cli.py`、`tests/test_bac_core.py`、`README.md`、`README.zh-CN.md`、`docs/bac-tutorial.md`、`CHANGELOG.md`、`pyproject.toml`。避免实现完整区块链、上传完整 `.bac`、引入数据库服务端、修改发布流程。

**Success Criteria:** `bac anchor` 可以生成盲化 anchor 请求、导入服务端 receipt 并追加 anchored checkpoint；`bac verify` 可以校验本地哈希链、receipt 签名、receipt 时间和 receipt 对应的 previous head；anchor 请求不包含项目名、路径、diff、actor、payload 或原始 `head_hash`；缺失或伪造 receipt 时验证失败或给出明确警告。

**Verification Plan:** 运行 `python -m pytest -q`；运行 CLI 端到端测试：初始化账本、记录事件、生成 anchor 请求、导入测试 receipt、验证通过；篡改 receipt 签名、anchor hash、previous head 后验证失败。

---

## Recommended Design

默认推荐实现 `private-anchor`，不是区块链。

客户端发送给外部服务的最小请求：

```json
{
  "format": "bac.anchor.request.v1",
  "anchor_hash": "sha256:<blinded-digest>",
  "client_created_at": "2026-05-30T00:00:00Z",
  "client_public_key": "optional-per-ledger-public-key",
  "sequence": 12
}
```

外部服务返回：

```json
{
  "format": "bac.anchor.receipt.v1",
  "anchor_hash": "sha256:<blinded-digest>",
  "server_created_at": "2026-05-30T00:00:03Z",
  "service": "bac-anchor",
  "key_id": "bac-anchor-ed25519-2026-01",
  "signature_alg": "Ed25519",
  "signature": "<base64-signature>"
}
```

盲化摘要计算：

```text
anchor_hash = sha256(canonical_json({
  "domain": "bac.anchor.v1",
  "ledger_nonce": ledger_nonce,
  "head_hash": head_hash
}))
```

`ledger_nonce` 只保存在本地 `.bac` 或本地配置中。外部服务只看到随机样式的摘要和时间，不看到项目内容。

## Privacy Position

默认不上传：

- `.bac` 文件内容
- 原始 `head_hash`
- 项目名、仓库 URL、分支、commit
- 文件路径、diff、payload、prompt、actor
- 用户真实身份

仍然可能暴露：

- 锚定时间
- 锚定频率
- 请求 IP
- 如果启用账户，则暴露账户关联
- 如果启用 per-ledger public key，则服务可知道同一匿名账本的连续活动

推荐默认使用 per-ledger pseudonymous key。它比完全匿名强，因为服务可以维护单个匿名账本的递增 sequence，减少事后分叉和重放；又比实名账户隐私好，因为不需要知道项目和用户身份。

## Threat Model

能防：

- 创作者在锚定时间之后重写 `.bac` 历史，并声称这是当时的历史。
- 篡改 anchored checkpoint 之前的事件。
- 伪造服务端 receipt。
- 用旧 receipt 冒充新 head。

不能防：

- 创作者在锚定前就不记录某些操作。
- 创作者完全放弃旧账本，重新开始一个新账本。
- 外部服务观察锚定时间和频率。
- 创作者控制本机后删除本地 receipt；审计方应要求提交可验证 receipt 或查询透明日志。

## Task: Anchor Data Model

**Files:**

- Create: `src/bac/core/anchor.py`
- Modify: `src/bac/core/schema.py`
- Test: `tests/test_bac_core.py`

**Steps:**

- 写 `compute_anchor_hash(head_hash, ledger_nonce)` 的失败测试，要求结果稳定、不是原始 head hash、输入变化后摘要变化。
- 实现 anchor 请求、receipt 的最小结构校验。
- 增加 receipt 与 checkpoint previous head 的绑定测试。

## Task: Receipt Signature Verification

**Files:**

- Modify: `pyproject.toml`
- Modify: `src/bac/core/anchor.py`
- Test: `tests/test_bac_core.py`

**Steps:**

- 在 `pyproject.toml` 增加可选依赖 `anchor = ["cryptography>=42"]`。
- 实现 Ed25519 receipt 签名验证。
- 测试有效签名通过、错误 key、错误 signature、被改写 receipt 均失败。
- 当未安装可选依赖时，给出明确错误，不静默当作通过。

## Task: Anchored Checkpoint

**Files:**

- Modify: `src/bac/service/event_builder.py`
- Modify: `src/bac/core/verify.py`
- Test: `tests/test_bac_core.py`

**Steps:**

- 复用现有 `checkpoint` 事件，允许 payload/evidence 携带 `anchor_receipt`。
- `bac verify` 检查 receipt 的 `anchor_hash` 是否等于 `prev_event_hash` 经 `ledger_nonce` 盲化后的结果。
- `anchor_status` 细分为 `not_anchored`、`local_checkpoint`、`receipt_valid`、`receipt_invalid`。
- 保持没有外部 receipt 的现有本地 checkpoint 兼容。

## Task: CLI Workflow

**Files:**

- Modify: `src/bac/adapters/cli.py`
- Test: `tests/test_bac_core.py`

**Steps:**

- 新增 `bac anchor request --json`，输出可发送给服务端的最小 anchor request。
- 新增 `bac anchor import --receipt-file receipt.json`，验证 receipt 后追加 anchored checkpoint。
- 新增 `bac verify --require-anchor`，用于审计场景强制要求有效外部 receipt。
- 端到端测试覆盖 init、record、anchor request、receipt import、verify。

## Task: Documentation

**Files:**

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/bac-tutorial.md`
- Modify: `CHANGELOG.md`

**Steps:**

- 说明 BAC 仍是 tamper-evident，不表述为绝对不可篡改。
- 增加 private-anchor 工作流示例。
- 明确隐私边界：只发送盲化摘要，不上传内容和路径。
- 明确外部锚定的证明含义：证明某个历史在某时间点已经存在，不证明所有真实操作都被记录。

## Rollback

如果实现过程中发现签名依赖、CLI 体验或隐私边界不够清晰，先只合入 `anchor.py` 的纯本地数据模型和文档，不发布网络协议。外部锚定命令保持实验性，直到 receipt 验证和隐私说明稳定。

## Open Decisions

- `ledger_nonce` 存在 `.bac` manifest、单独本地配置，还是两者都支持。
- 是否默认启用 per-ledger pseudonymous key。
- 官方 anchor service 是否由本仓库提供，还是只定义协议并允许第三方服务实现。
- 是否需要公开透明日志；建议作为第二阶段，不进入第一版。
