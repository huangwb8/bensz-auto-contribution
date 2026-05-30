# BAC Local/Remote Anchor Server Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 BAC 增加本地模式与默认本地+远程模式，并在 `./server` 托管可 Docker 部署的隐私保护型锚定服务和现代化后台，使创作者无法在锚定时间之后单方面重写一套无痕历史，同时避免上传 `.bac` 内容、文件路径、项目名、diff 或提示词。

**Architecture:** BAC 采用本地优先架构：本地 `.bac` 继续保存完整事件链和验证所需上下文，远程服务只作为可信时间、签名 receipt 和审计查询的增强层。默认运行模式为 `hybrid`，客户端在写入本地 `.bac` 后尝试向服务端提交盲化后的 `anchor_hash`，服务端返回带服务端时间戳和 Ed25519 签名的 receipt；用户也可以显式选择 `local` 纯本地模式。本地用 `checkpoint` 事件保存 receipt，验证器可离线校验 receipt 签名，并检查 receipt 是否锚定了该 checkpoint 之前的 `head_hash`。

**Tech Stack:** Python 3.10+，现有 ZIP v2 BAC 容器，canonical JSON，SHA-256，Ed25519；本地端新增可选 `cryptography` extra 用于 receipt 签名验证。服务端放在 `./server`，建议使用 FastAPI、SQLite 默认存储、可选 PostgreSQL、React/Vite 管理后台、Dockerfile 与 Docker Compose；测试继续使用 `unittest`/`pytest`、CLI 子进程端到端测试和服务端 API 测试。

**Minimal Change Scope:** 允许修改 `src/bac/core/`、`src/bac/service/`、`src/bac/adapters/cli.py`、`tests/`、`server/`、`README.md`、`README.zh-CN.md`、`docs/bac-tutorial.md`、`CHANGELOG.md`、`pyproject.toml`。避免实现完整区块链、上传完整 `.bac`、上传文件路径或 diff、引入复杂计费系统、多租户 RBAC 或修改 PyPI 发布流程。

**Success Criteria:** `bac init` 支持选择 `local` 或 `hybrid` 模式，默认 `hybrid`；`bac anchor push` 可以向配置的服务端提交盲化 anchor 请求、接收 signed receipt 并追加 anchored checkpoint；`bac anchor request/import` 保留为离线或手动流程；`bac verify` 可以校验本地哈希链、receipt 签名、receipt 时间和 receipt 对应的 previous head；`./server` 可以用 Docker 启动，提供 anchor API、公钥查询、receipt 查询和后台页面；anchor 请求不包含项目名、路径、diff、actor、payload 或原始 `head_hash`；缺失或伪造 receipt 时验证失败或给出明确警告。

**Verification Plan:** 运行 `python -m pytest -q`；运行 CLI 端到端测试：初始化账本、记录事件、配置 hybrid 服务端、push anchor、保存 receipt、验证通过；运行服务端 API 测试：创建 anchor、查询 receipt、查询 public keys、验证签名；运行 Docker Compose smoke test：`docker compose -f server/docker-compose.yml up --build` 后健康检查通过；篡改 receipt 签名、anchor hash、previous head 后验证失败。

---

## Recommended Design

默认推荐实现 `local-first + private-anchor`，不是区块链，也不是把 `.bac` 上传到中心化平台。

BAC 有两种运行模式：

- `local`：只写入本地 `.bac`，支持本地 hash chain、local checkpoint 和本地 verify。适合离线、内网、隐私要求极高或不想部署服务端的用户。
- `hybrid`：默认模式。本地 `.bac` 仍是唯一完整账本，客户端额外向远程服务提交盲化 `anchor_hash`，服务端返回 signed receipt。本地记录不因远程服务短暂不可用而失败，但正式审计可以用 `bac verify --require-anchor` 强制要求有效 receipt。

远程服务端是可信增强层，负责：

- 接收盲化 `anchor_hash`。
- 记录服务端时间戳和递增 sequence。
- 使用服务端 Ed25519 key 签名 receipt。
- 提供 receipt 查询和 public key 查询。
- 提供后台查看锚定时间线、验证状态和服务健康度。

远程服务端不负责：

- 保存完整 `.bac`。
- 保存项目名、仓库 URL、分支、commit。
- 保存文件路径、diff、payload、prompt。
- 判断最终贡献归属或法律责任。

客户端发送给外部服务的最小请求：

```json
{
  "format": "bac.anchor.request.v1",
  "anchor_hash": "sha256:<blinded-digest>",
  "client_created_at": "2026-05-30T00:00:00Z",
  "ledger_public_key": "optional-per-ledger-public-key",
  "ledger_id": "optional-pseudonymous-ledger-id",
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
  "receipt_id": "bac_receipt_20260530T000003Z_...",
  "sequence": 12,
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

## Client Modes

本地端需要把模式选择显式写入配置或 `.bac` manifest 的非敏感元数据中，避免用户误以为已经远程锚定。

推荐配置语义：

```text
bac config set mode hybrid
bac config set anchor.url https://anchor.example.com
bac config set anchor.require false
```

CLI 行为：

- `bac init` 默认使用 `hybrid`，如果没有配置 `anchor.url`，初始化仍成功，并提示远程锚定尚未配置。
- `bac record` 只负责追加本地事件，不因为远程服务失败而丢失本地记录。
- `bac anchor push` 主动提交当前 head 的盲化摘要并保存 receipt。
- `bac verify` 默认验证本地链和已存在 receipt；没有 receipt 时给 warning。
- `bac verify --require-anchor` 在审计场景强制要求至少一个有效远程 receipt。
- `bac anchor request/import` 保留给离线提交、私有网络跳板或第三方服务集成。

## Server Scope

`./server` 是官方可选服务端实现，目标是让用户能把 BAC 部署成“完整体”。

推荐目录：

```text
server/
  app/
    main.py
    api/
    core/
    db/
    signing/
  web/
    src/
    package.json
  tests/
  Dockerfile
  docker-compose.yml
  README.md
```

服务端 API MVP：

```text
GET  /healthz
GET  /api/v1/public-keys
POST /api/v1/anchors
GET  /api/v1/receipts/{receipt_id}
GET  /api/v1/ledgers/{ledger_id}/receipts
```

数据表 MVP：

- `signing_keys`：`key_id`、public key、状态、创建时间、轮换时间。
- `anchors`：`receipt_id`、`anchor_hash`、`ledger_id`、`sequence`、server timestamp、key_id、signature。
- `audit_events`：服务端自身的系统事件，例如 key rotation、receipt creation、verification failure。

后台 MVP：

- 服务健康状态和当前签名公钥。
- receipt 列表、锚定时间线和签名验证状态。
- ledger 详情页，以匿名 ledger id 聚合 receipt。
- anchor 请求频率、失败请求和最近错误。
- key rotation 状态和只读审计事件。

部署 MVP：

- `server/Dockerfile` 构建 API 和静态后台。
- `server/docker-compose.yml` 默认使用 SQLite volume，提供可选 PostgreSQL profile。
- 通过环境变量配置 `BAC_ANCHOR_ADMIN_TOKEN`、`BAC_ANCHOR_DB_URL`、`BAC_ANCHOR_PRIVATE_KEY_PATH`、`BAC_ANCHOR_PUBLIC_BASE_URL`。
- 服务端私钥只保存在服务端 volume 或外部 secret，不写入仓库。

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

后台默认也只能展示服务端可见的最小数据。除非后续用户显式开启项目名或组织元数据上传，否则后台不应出现项目路径、仓库 URL、文件名、diff 或 prompt。

## Threat Model

能防：

- 创作者在锚定时间之后重写 `.bac` 历史，并声称这是当时的历史。
- 篡改 anchored checkpoint 之前的事件。
- 伪造服务端 receipt。
- 用旧 receipt 冒充新 head。
- 服务端私钥未泄露时，攻击者伪造官方锚定历史。

不能防：

- 创作者在锚定前就不记录某些操作。
- 创作者完全放弃旧账本，重新开始一个新账本。
- 外部服务观察锚定时间和频率。
- 创作者控制本机后删除本地 receipt；审计方应要求提交可验证 receipt 或查询透明日志。
- 服务端管理员恶意删除数据库中的查询记录；本地已保存的 signed receipt 仍可离线验证，但在线查询连续性会受影响。后续透明日志可进一步增强。

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
- 新增 `bac anchor push`，读取 `anchor.url` 后直接提交 request、接收 receipt 并追加 anchored checkpoint。
- 新增 `bac config` 或等价配置入口，支持 `mode`、`anchor.url`、`anchor.require`。
- 新增 `bac verify --require-anchor`，用于审计场景强制要求有效外部 receipt。
- 端到端测试覆盖 local 模式、hybrid 模式、anchor request/import、anchor push、verify。

## Task: Server API

**Files:**

- Create: `server/app/main.py`
- Create: `server/app/api/anchors.py`
- Create: `server/app/signing/ed25519.py`
- Create: `server/app/db/models.py`
- Create: `server/tests/test_anchor_api.py`

**Steps:**

- 实现 `GET /healthz`。
- 实现 `GET /api/v1/public-keys`，返回当前验签所需的 public key 和 `key_id`。
- 实现 `POST /api/v1/anchors`，校验 request schema、写入数据库、生成 signed receipt。
- 实现 `GET /api/v1/receipts/{receipt_id}`，供审计方查询服务端见过的 receipt。
- 测试 receipt 签名可被本地端校验，重复 `anchor_hash + ledger_id + sequence` 行为明确且稳定。

## Task: Server Storage and Key Management

**Files:**

- Create: `server/app/db/session.py`
- Create: `server/app/core/config.py`
- Create: `server/app/signing/keys.py`
- Test: `server/tests/test_key_management.py`

**Steps:**

- 默认使用 SQLite 文件数据库，数据库路径来自 `BAC_ANCHOR_DB_URL`。
- 支持通过环境变量或 mounted secret 加载 Ed25519 private key。
- 首次启动时如果未提供 key，在开发模式生成本地 key；生产模式必须显式提供 key 或 secret。
- 记录 key rotation 元数据，但第一版只要求单 active key。
- 确保 private key 不进入日志、response、`.bac` 或测试 fixture。

## Task: Admin Console

**Files:**

- Create: `server/web/package.json`
- Create: `server/web/src/`
- Modify: `server/app/main.py`
- Test: `server/tests/test_admin_static.py`

**Steps:**

- 使用 React/Vite 构建现代化后台，并由 FastAPI 静态托管构建产物。
- 首页展示服务健康、当前 key id、receipt 总数、最近锚定时间。
- receipt 列表支持按 `ledger_id`、`receipt_id`、时间范围过滤。
- ledger 详情页展示匿名锚定时间线和 sequence 连续性。
- 后台只读，第一版用 `BAC_ANCHOR_ADMIN_TOKEN` 做简单保护，不引入复杂 RBAC。

## Task: Docker Deployment

**Files:**

- Create: `server/Dockerfile`
- Create: `server/docker-compose.yml`
- Create: `server/README.md`
- Test: `server/tests/test_deployment_docs.py`

**Steps:**

- Dockerfile 构建 Python API 和前端静态资源。
- Compose 默认启动单容器服务和 SQLite volume。
- 提供可选 PostgreSQL profile，但不作为第一版默认依赖。
- README 写清楚环境变量、key 生成、升级、备份和健康检查。
- smoke test 至少覆盖容器启动后 `/healthz` 返回成功。

## Task: Documentation

**Files:**

- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/bac-tutorial.md`
- Modify: `CHANGELOG.md`

**Steps:**

- 说明 BAC 仍是 tamper-evident，不表述为绝对不可篡改。
- 增加 local 模式、hybrid 模式和 private-anchor 工作流示例。
- 增加 `./server` Docker 部署说明和后台入口。
- 明确隐私边界：只发送盲化摘要，不上传内容和路径。
- 明确外部锚定的证明含义：证明某个历史在某时间点已经存在，不证明所有真实操作都被记录。

## Rollback

如果实现过程中发现签名依赖、CLI 体验或隐私边界不够清晰，先只合入 `anchor.py` 的纯本地数据模型和文档，不发布网络协议。若服务端实现风险超出预期，先发布本地端的 `anchor request/import` 和协议文档，把 `./server` 保持为实验性 preview，直到 receipt 验证、密钥管理、Docker 部署和隐私说明稳定。

## Open Decisions

- `ledger_nonce` 存在 `.bac` manifest、单独本地配置，还是两者都支持。
- 是否默认启用 per-ledger pseudonymous key。
- `hybrid` 默认失败策略是仅 warning，还是允许用户设置为 fail-closed。
- 官方 anchor service 是否长期由本仓库提供，还是本仓库只提供自托管 reference server。
- 后台第一版是否需要用户体系；当前建议只做单管理员 token。
- 是否需要公开透明日志；建议作为第二阶段，不进入第一版。
