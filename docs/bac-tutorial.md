# BAC 工作原理教程

本文面向第一次阅读 `.bac` 文件的人，解释当前 `bac` 包如何记录贡献、每条记录里的字段是什么意思，以及验证器为什么能发现常见篡改。

## 核心概念

`.bac` 是一个单文件 ZIP 容器，默认文件名是 `project.bac`。用户通常只需要看到这一个文件；工具会在容器内部维护 manifest、事件序列和后续可扩展的证据文件。

当前 v2 容器至少包含：

```text
manifest.json
events/000000000001.json
events/000000000002.json
```

`manifest.json` 记录容器版本、事件格式、项目绑定信息、初始事件 hash 和存储约定。`events/` 下每个文件是一条 canonical JSON 事件，文件名从 `000000000001.json` 开始连续递增。当前实现不会原地修改历史事件，而是为新事件写入新的内部事件条目。

一个 `.bac` 文件可以理解为项目贡献时间线：

```text
genesis -> human_instruction -> ai_generation -> file_change -> test_result -> checkpoint
```

每条事件都会记录：

- 谁或什么来源产生了这条记录
- 这条记录描述了什么贡献或证据
- 当前项目上下文是什么
- 前一条事件的 hash 是什么
- 当前事件自身的 hash 是什么

这使 `.bac` 具备 tamper-evident 能力：它不能阻止别人编辑 ZIP 文件，但编辑、插入、删除、复制隐藏内部条目或重排历史事件后，验证器可以发现容器结构异常、hash 不匹配或链条断裂。

## 受限尾部修复

`bac repair stale-tail` 用于处理历史账本中已经存在的机械性尾部分叉，例如工具基于旧 head 写入事件、并发追加或 git 回退/合并导致最后几条事件的 `prev_event_hash` 接到了较早的 head。

该命令不是通用历史重写工具。它默认 dry-run，只在能够按容器事件顺序唯一证明为尾部 stale-head 断链时生成计划。允许变更的字段只有：

- `prev_event_hash`
- `event_hash`

其它字段，包括 `event_id`、`event_type`、`source_type`、`trust_level`、`actor`、`payload`、`evidence`、`redactions` 和 `signature`，都不能被 repair 修改。若事件内容被篡改、归因字段被改写、断链位于中间、尾部包含 signed/anchored/checkpointed 事件，或修复后仍无法通过验证，命令会拒绝执行。

实际执行 `--apply` 后，BAC 会先重写修复后的 ZIP 容器，再追加一条 `tool_command/source_type=tool` 的 repair record，并追加本地 checkpoint，保留修复行为本身的审计线索。

## 快速流程

先安装本地包：

```bash
python -m pip install -e .
```

在目标项目中初始化：

```bash
bac init
```

记录用户需求：

```bash
bac record \
  --event-type human_instruction \
  --source-type human \
  --summary "用户要求添加 BAC 验证流程"
```

AI tool 宿主收到用户输入框消息时，应优先用 `bac input record` 记录低敏人类输入证据：

```bash
bac input record \
  --host codex \
  --session-id s1 \
  --message-index 1 \
  --message-file /tmp/user-message.txt
```

历史 `Prompts.md`、聊天导出或 issue/PR 评论可以作为补充导入来源，但不应成为系统正确性的前提：

```bash
bac input import-log --source-file Prompts.md
```

记录 AI 生成或修改：

```bash
bac record \
  --event-type ai_generation \
  --source-type ai \
  --summary "AI 生成哈希链验证实现"
```

记录文件变更证据：

```bash
bac record \
  --event-type file_change \
  --source-type ai \
  --summary "更新验证器逻辑" \
  --path src/bac/core/verify.py
```

记录工具执行结果：

```bash
bac record \
  --event-type test_result \
  --source-type tool \
  --summary "单元测试通过" \
  --command-text "python -m pytest -q" \
  --exit-code 0
```

记录本地 checkpoint：

```bash
bac record \
  --event-type checkpoint \
  --source-type system \
  --summary "记录当前 head hash"
```

验证账本：

```bash
bac verify
```

为机械性 stale-tail 尾部分叉生成修复计划。默认只输出计划，不写入文件：

```bash
bac repair stale-tail --json
```

确认计划后再显式应用：

```bash
bac repair stale-tail --json --apply
```

查看时间线：

```bash
bac inspect
```

生成隐私保护 anchor request：

```bash
bac anchor request --json
```

导入远程服务返回的 signed receipt，并在审计场景要求有效 receipt：

```bash
bac anchor import --receipt-file receipt.json --public-key "$ANCHOR_PUBLIC_KEY"
bac verify --require-anchor
```

## 一条事件的结构

下面是简化后的事件示例：

```json
{
  "format": "bac.event.v2",
  "event_id": "bac_20260528T020756Z_c77031f6bcee4466",
  "event_type": "file_change",
  "source_type": "ai",
  "trust_level": "observed",
  "created_at": "2026-05-28T02:07:56Z",
  "project": {
    "root_path": "/path/to/project",
    "root_hash": "sha256:...",
    "git_remote": "git@github.com:example/repo.git",
    "git_commit": "abc123...",
    "git_branch": "main",
    "worktree_dirty": true
  },
  "actor": {
    "declared_name": "ai",
    "declared_kind": "ai",
    "session_id": "optional-session"
  },
  "payload": {
    "summary": "更新验证器逻辑",
    "files": [
      {
        "path": "src/bac/core/verify.py",
        "exists": true,
        "after_hash": "sha256:..."
      }
    ]
  },
  "evidence": [
    {
      "type": "git_diff_summary",
      "hash": "sha256:...",
      "redacted": false,
      "summary": " src/bac/core/verify.py | 10 +++++-----"
    }
  ],
  "redactions": [],
  "prev_event_hash": "sha256:...",
  "event_hash": "sha256:...",
  "signature": null
}
```

## 顶层字段含义

`format`

固定为 `bac.event.v2`。验证器用它判断当前事件是否属于支持的 BAC 事件格式。

`event_id`

事件唯一标识，当前由 UTC 时间戳和随机后缀组成，例如 `bac_20260528T020756Z_c77031f6bcee4466`。它方便人类定位事件，但 hash 链安全性不依赖它的不可预测性。

`event_type`

事件类型，描述这条记录“在贡献流程里代表什么”。当前支持：

- `genesis`：账本初始化事件，只能作为第一条事件
- `session_started`：会话开始
- `human_instruction`：人类需求、约束或指令
- `ai_plan`：AI 计划
- `ai_generation`：AI 生成内容、方案或代码
- `tool_command`：工具命令执行
- `file_snapshot`：文件快照
- `file_change`：文件变更
- `test_result`：测试或验证结果
- `human_review`：人类审阅意见
- `human_approval`：人类授权或确认
- `checkpoint`：记录当前 head hash，降低尾部截断风险
- `verification`：验证动作或验证结果

`source_type`

来源类型，回答“这条记录的直接来源是谁”。当前必须是以下四类之一：

- `human`：人类输入，例如需求、约束、审阅、批准、手写修改
- `ai`：AI 的推理、计划、生成、重构建议或修复意图
- `tool`：命令、测试、格式化器、linter、git 等工具输出
- `system`：BAC 工具自身或运行环境产生的系统事件

`source_type` 记录直接来源，不是偏好的署名标签。人类批准、合并或授权 AI 产物时，不应把 AI 生成内容改写为 `human`；正确做法是保留一条 `ai_generation/source_type=ai`，再追加一条 `human_approval/source_type=human`。这种把 AI 或工具实际产物伪装为人类创作的做法称为贡献来源漂白。

`human_approval` 可以用 payload 链接被批准的前序事件：

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

其中 `approves_event_hash` 必须指向同一账本中已经存在的前序事件。批准事实不会改变被批准事件的创作来源。

`trust_level`

信任等级，描述这条记录的可信依据。当前支持：

- `declared`：声明型记录，例如人类需求或 AI 生成说明
- `observed`：由工具观察到的事实，例如文件 hash、命令退出码、git diff 摘要
- `signed`：预留给签名事件；当前事件签名尚未实现，普通事件不能自称为 `signed`
- `verified`：验证或 checkpoint 类事件
- `anchored`：仅用于带有效远程 receipt 的 checkpoint 事件

当前默认规则是：`human` 和 `ai` 的普通贡献通常是 `declared`；文件变更、测试结果和工具命令通常是 `observed`；普通本地 `checkpoint` 是 `verified`。`bac record` 不允许直接创建 `signed` 或 `anchored`，其中 `anchored` 只能由 `bac anchor import` 或 `bac anchor push` 在 receipt 验签后生成。

`created_at`

事件创建时间，格式是 UTC ISO-8601，必须以 `Z` 结尾。验证器会检查时间格式，并在发现时间倒退时给出 warning。

`project`

项目上下文，用于把事件绑定到具体项目。它不是完整项目快照，而是记录关键上下文：

- `root_path`：项目根路径。若在 git 仓库内运行，优先使用 git 仓库根目录
- `root_hash`：基于 `root_path` 和 `git_remote` 计算出的项目绑定 hash
- `git_remote`：当前仓库的 `remote.origin.url`，不存在时为 `null`
- `git_commit`：当前 `HEAD` commit，不在 git 仓库或无 commit 时为 `null`
- `git_branch`：当前分支名，不存在时为 `null`
- `worktree_dirty`：运行时工作区是否有未提交改动

验证器会要求同一个 `.bac` 文件里的 `project.root_hash` 保持一致，从而发现账本被混入其它项目事件的情况。

`actor`

声明的操作者信息。默认由 CLI 参数生成：

- `declared_name`：声明名称
- `declared_kind`：声明类型
- `session_id`：可选会话标识

注意：当前 v2 里 `actor` 是声明信息，不等同于强身份认证。需要身份真实性时，应结合未来签名或外部可信身份机制。

`payload`

事件主体内容。至少会包含 `summary`。不同参数会写入不同内容：

- `--summary` 会写入 `payload.summary`
- `--path` 会收集文件快照并写入 `payload.files`
- `--command-text` 会写入 `payload.command`
- `--exit-code` 会写入 `payload.exit_code`
- `--payload-json` 会先作为附加对象写入 payload，再由 `summary` 覆盖或补充摘要
- `checkpoint` 事件会额外写入 `payload.checkpointed_head_hash`

`evidence`

证据列表。当前主要包括通过 `--path` 收集到的 `git diff --stat` 摘要。每个证据对象通常包含：

- `type`：证据类型，例如 `git_diff_summary`
- `hash`：证据内容的 sha256
- `redacted`：证据是否经过脱敏
- `summary`：人类可读的证据摘要

也可以用 `--evidence-json` 传入额外证据列表。

`redactions`

脱敏记录。BAC 会在写入前扫描 payload 和 evidence，遇到私钥、常见 API key、token、password、authorization、cookie 或过长字符串时进行脱敏或截断，并在这里记录脱敏路径和原因。

示例：

```json
[
  {
    "path": "$.command",
    "reason": "sensitive_pattern"
  }
]
```

`prev_event_hash`

前一条事件的 `event_hash`。第一条 `genesis` 事件必须是 `null`。从第二条事件开始，它必须等于上一条事件的 `event_hash`。

`event_hash`

当前事件的 sha256。计算时会先把事件转成 canonical JSON，再排除 `event_hash` 字段本身，避免循环引用。只要事件内容被修改，重新计算出的 hash 就会变化。

`signature`

事件顶层签名字段。当前 v2 要求它可以是 `null` 或对象，但还没有实现通用事件签名验证。验证器遇到非空事件签名会报告当前尚不支持签名验证。远程 anchor receipt 的 Ed25519 签名不放在这里，而是放在 `checkpoint` 事件的 `payload.anchor.anchor_receipt` 中。

## CLI 参数如何变成事件

全局参数：

`--root`

目标项目根目录。默认是当前目录。所有相对路径 `.bac` 文件和 `--path` 文件快照都会基于它解析。

`--bac-file`

账本容器文件路径。默认是 `project.bac`。如果传相对路径，会放在 `--root` 下；如果传绝对路径，则直接使用该路径。

`init --force`

初始化账本。如果目标 `.bac` 已存在且非空，默认拒绝覆盖；传 `--force` 会重写。

`init --mode`

设置账本锚定模式，支持 `local` 和 `hybrid`。默认是 `hybrid`，但没有配置 `anchor.url` 时不会自动联网。

`init --anchor-url`

初始化时写入锚定服务地址，供后续 `bac anchor push` 使用。

`init --actor-name` 与 `init --actor-kind`

设置 genesis 事件的 actor。默认是 `bac` 和 `system_tool`。

`record --event-type`

设置事件类型。不能手动记录 `genesis`，因为 `genesis` 只由 `init` 创建。

`record --source-type`

设置来源类型，必须是 `human`、`ai`、`tool`、`system` 之一。

`record --trust-level`

手动指定信任等级。不传时使用默认规则。

`record --summary`

必填。写入 `payload.summary`，也是 `inspect` 时间线展示的主要文本。

`record --path`

可以重复传多次。BAC 会对每个路径记录：

- 相对路径
- 文件是否存在
- 文件内容 sha256
- 如果路径存在但不是普通文件，会标记 `kind: non_file`

同时，如果目标目录是 git 仓库，BAC 会尝试记录这些路径的 `git diff --stat` 摘要到 `evidence`。

`record --command-text` 与 `record --exit-code`

记录命令文本和退出码。BAC 当前不会替你执行命令，只记录你传入的命令和结果。因此调用方应先执行命令，再把结果写入 BAC。

`record --payload-json`

传入额外 payload 对象。必须是 JSON object，例如：

```bash
bac record \
  --event-type ai_plan \
  --source-type ai \
  --summary "制定实现计划" \
  --payload-json '{"risk":"low","scope":"docs"}'
```

`record --evidence-json`

传入额外 evidence 列表。必须是 JSON object 数组，例如：

```bash
bac record \
  --event-type verification \
  --source-type tool \
  --summary "外部检查完成" \
  --evidence-json '[{"type":"manual_check","summary":"reviewed by maintainer"}]'
```

`record --actor-name`、`record --actor-kind`、`record --session-id`

设置 actor 信息。不传时，`actor-name` 和 `actor-kind` 默认使用 `source_type`。

`input record`

记录 AI tool 宿主刚收到的用户输入。它会生成 `source_type=human` 事件，并自动写入：

- `payload.input_provenance.format = bac.human_input.v1`
- `channel`，默认是 `ai_tool_user_message`
- 可选 `host`、`session_id` 和 `message_index`
- `message_hash`，基于 BAC 域分离后的规范化文本计算
- `recorded_full_text = false`
- `classification`，可为 `instruction`、`review` 或显式 `approval`
- 脱敏 `summary` 和脱敏 `excerpt`

默认不会保存完整 prompt。`message_hash` 可用于审计和幂等跳过，但短 prompt 或容易猜测的 prompt 仍可能被字典验证，因此它不是零泄露隐私保证。

`input import-log`

从项目内 prompt log 补充导入人类输入证据。`--source-file` 必须位于项目根目录内，写入 `.bac` 时只记录相对路径、行号、消息 hash 和脱敏摘录。重复导入同一来源区块会被跳过。

`--json`

让命令输出 machine-readable JSON，方便 AI tool 或脚本调用。

`config set`

追加一条配置事件，当前支持 `mode`、`anchor.url`、`anchor.require`、`anchor.ledger_id` 和 `cloud.auto_anchor`：

```bash
bac config set mode hybrid
bac config set anchor.url http://localhost:8080
bac config set anchor.require true
```

`cloud register/login/link/status`

BAC Cloud 工作流用于把本地 `.bac` 账本绑定到你部署的 BAC 服务端。用户可以通过 CLI 注册或登录，也可以访问服务端 `/cloud` 页面在 GUI 中拿到 token。CLI token 保存在本机用户配置目录，默认是 `~/.config/bac/credentials.json`，不会写入 `.bac`。

```bash
bac cloud register --url https://bac.example.com --email user@example.com
bac cloud login --url https://bac.example.com --email user@example.com
bac cloud link --url https://bac.example.com --ledger-name my-project
bac cloud status
```

`cloud link` 会在服务端创建 cloud ledger，在本地追加配置事件，并立即对绑定后的账本 head 执行一次远程锚定：

- `mode: hybrid`
- `anchor.url: https://bac.example.com`
- `anchor.require: true`
- `anchor.ledger_id: 服务端返回的 ledger id`
- `cloud.auto_anchor: true`

之后正常使用 `bac record` 追加事件时，CLI 会自动执行一次锚定：本地完整 `.bac` 仍保留在项目中；服务端只接收盲化 `anchor_hash`、ledger id、sequence 和低敏 `client_summary`。`client_summary` 包含事件数量、来源计数、信任等级计数和当前 head 事件类型，不包含路径、diff、payload、prompt、actor、项目名或原始 `head_hash`。

如果 `cloud link` 显式传入 `--allow-insecure-anchor-url`，本地配置会记录 `anchor.allow_insecure: true`，仅用于本地开发环境后续自动锚定同一个非 HTTPS/内网地址。生产环境应使用 HTTPS 公网地址。

`anchor request`

基于当前 head 和本地 `ledger_nonce` 生成最小 anchor request。请求只包含盲化后的 `anchor_hash`、客户端时间、可选匿名账本信息和 sequence，不包含项目名、路径、diff、payload、prompt、actor 或原始 `head_hash`。

`anchor import`

读取锚定服务返回的 receipt，使用传入的 Ed25519 public key 验签，然后追加 `trust_level: anchored` 的 checkpoint 事件。

`anchor push`

读取 `anchor.url`，向服务端发送 anchor request，接收 receipt，获取或使用指定 public key 验签，然后追加 anchored checkpoint。远程失败不会影响已经存在的本地记录。

默认情况下，`anchor push` 只允许 `https://` 且非 loopback、link-local、private、multicast、reserved 的公网地址；域名会先解析 A/AAAA 记录，只要任一结果指向非公网地址就会拒绝，以降低 SSRF 和误发内网请求风险。本地开发时可以显式使用：

```bash
bac anchor push --allow-insecure-anchor-url
```

生产锚定服务如要求写入 token，可以使用：

```bash
bac anchor push --token "$BAC_ANCHOR_API_TOKEN"
```

也可以直接设置环境变量 `BAC_ANCHOR_API_TOKEN`。不要把 token 写入 `.bac` 配置或提交到仓库。

`verify --require-anchor`

强制要求至少存在一个有效远程 receipt。适用于正式审计；普通 `bac verify` 仍会允许纯本地账本通过或给出 warning。

如果账本配置中存在 `anchor.require true`，即使命令行没有传 `--require-anchor`，`bac verify` 也会按正式审计语义要求有效远程 receipt。

## 哈希链原理

BAC 的哈希链有两个关键字段：

```text
prev_event_hash -> event_hash
```

第一条 `genesis`：

```text
prev_event_hash = null
event_hash = hash(genesis_without_event_hash)
```

第二条事件：

```text
prev_event_hash = genesis.event_hash
event_hash = hash(second_event_without_event_hash)
```

第三条事件：

```text
prev_event_hash = second.event_hash
event_hash = hash(third_event_without_event_hash)
```

验证时，BAC 会从头到尾做两件事：

- 重新计算每条事件的 `event_hash`
- 检查每条事件的 `prev_event_hash` 是否等于上一条事件的 `event_hash`

因此：

- 修改历史事件内容会导致 `event_hash mismatch`
- 插入事件会导致后续 `prev_event_hash` 接不上
- 删除中间事件会导致后续 `prev_event_hash` 接不上
- 重排事件会导致链条断裂

不过，单纯本地哈希链不能完全防止尾部截断。例如攻击者删除最后几条事件后，剩余前缀本身仍可能是一条自洽链。当前 v2 用 `checkpoint` 表示“我在这里见过这个 head hash”，后续可以把 checkpoint head 发布到 git note、release artifact 或可信时间戳服务，以增强尾部截断发现能力。

## 隐私保护锚定原理

BAC 的默认锚定模式是 `hybrid`：完整 `.bac` 仍只保存在本地，远程服务只接收盲化摘要：

```text
anchor_hash = sha256(canonical_json({
  "domain": "bac.anchor.v1",
  "ledger_nonce": 本地 nonce,
  "head_hash": 当前账本 head
}))
```

`ledger_nonce` 保存在本地 `.bac` 配置和 anchored checkpoint 中。远程服务无法从 `anchor_hash` 反推出项目内容、路径、diff、prompt 或原始 `head_hash`。

服务端返回的 receipt 会用 Ed25519 签名固定的 canonical signing payload。验证器会检查：

- receipt schema 是否合法
- receipt 的 `anchor_hash` 是否绑定 checkpoint 前一条事件的 `event_hash`
- receipt 签名是否能被 checkpoint 记录的 public key 验证
- `--require-anchor` 是否满足至少一个有效 receipt

`anchor_status` 现在细分为：

- `not_anchored`：没有 checkpoint
- `local_checkpoint`：只有本地 checkpoint
- `receipt_valid`：至少一个远程 receipt 有效
- `receipt_invalid`：存在远程 receipt 但无法通过绑定或签名验证

## 验证器会检查什么

`bac verify` 会检查：

- `.bac` 文件存在
- `.bac` 是有效的 v2 ZIP 容器
- `.bac` 容器总大小、事件数量和单个 JSON 成员大小在安全上限内
- 容器包含 `manifest.json`
- 内部路径没有重复条目
- `events/` 事件文件名从 `000000000001.json` 开始连续递增
- `manifest.json` 与 genesis 事件一致
- 每个事件文件都是合法 JSON
- 每条事件都是 JSON object
- 必填字段齐全
- `format`、`event_type`、`source_type`、`trust_level` 合法
- `event_type` 与 `source_type` 的语义不冲突，例如 `ai_generation` 必须来自 `ai`，`human_approval` 必须来自 `human`
- `human_approval.payload.approves_event_hash` 如存在，必须指向同一账本中的前序事件
- 人类输入事件的 `payload.input_provenance` 格式、通道、消息 hash、`recorded_full_text` 和配套脱敏 evidence 结构合法
- `created_at` 是 UTC 时间
- `project` 字段结构合法
- `prev_event_hash` 和 `event_hash` 是 `sha256:<64位hex>` 或允许的 `null`
- 第一条事件是 `genesis`
- `genesis.prev_event_hash` 是 `null`
- 每条事件的 hash 可复算
- 每条事件正确连接到上一条事件
- 同一账本内 `project.root_hash` 不变
- checkpoint 的 `checkpointed_head_hash` 等于它自己的 `prev_event_hash`
- `signed` trust level 不能在签名实现前被伪造
- anchored checkpoint 的 receipt 绑定前序 head，且 Ed25519 签名有效
- `anchored` trust level 只能出现在带有效远程 receipt 的 checkpoint 上

验证结果有三种状态：

- `pass`：没有错误和警告
- `warn`：没有错误，但有警告，例如缺少 checkpoint，或存在 AI 活动但没有任何人类输入 provenance
- `fail`：存在错误，例如 hash 不匹配或链条断裂

## Inspect 时间线

`bac inspect` 不做深度验证，它只是读取事件并输出更容易阅读的时间线：

```text
2026-05-28T02:07:26Z  genesis  system/observed  Initialized BAC ledger
2026-05-28T02:07:26Z  human_instruction  human/declared  用户要求添加 BAC 验证流程
2026-05-28T02:07:56Z  test_result  tool/observed  单元测试通过
```

每行展示：

- `created_at`
- `event_type`
- `source_type/trust_level`
- `payload.summary`

使用 `--json` 时，带 `payload.input_provenance` 的人类事件会额外展示输入来源摘要，例如 `channel`、`host`、`source_path`、行号、`classification` 和 `message_hash`。

## 当前安全边界

当前 BAC v2 已支持：

- 单文件 ZIP 容器
- 追加式内部事件序列
- human、ai、tool、system 四类来源区分
- canonical JSON
- SHA-256 哈希链
- 项目上下文绑定
- 容器 manifest 校验
- 重复内部路径和事件编号缺口检测
- 文件快照 hash
- git diff 摘要证据
- 本地 checkpoint
- 人类输入 provenance 与 prompt log 补充导入
- 隐私保护 anchor request/import/push
- Ed25519 receipt 验签
- 敏感信息脱敏
- 账本验证和时间线查看

当前尚未实现：

- 通用事件顶层签名验证
- 公开透明日志
- 自动执行命令并捕获 stdout/stderr
- 对文件内容语义变化的判断
- 对 actor 声明身份的真实性证明

因此，当前 `.bac` 的正确理解是：它提供可审计、可复算、篡改可发现的贡献记录基础设施；它不是不可修改文件，也不是完整身份认证系统。

## 推荐记录方式

一次较完整的 AI 协作开发可以记录为：

```text
bac input record：用户提交给 AI tool 的低敏输入证据
human_instruction：用户需求和约束
ai_plan：AI 的实现计划
ai_generation：AI 生成或修改的内容摘要
file_change：涉及的关键文件快照
tool_command：重要工具命令
test_result：测试结果
human_review：用户或维护者审阅意见
human_approval：最终授权或确认
checkpoint：记录当前账本 head
```

如果人类最终采纳的是 AI 产物，推荐让 `human_approval.payload.approves_event_hash` 指向对应 `ai_generation` 的 `event_hash`。这样 `bac inspect --human` 会展示人类需求、审阅和批准事实，但不会把 AI 生成事件混入人类创作来源。

如果用户粘贴了日志、网页内容、AI 生成片段或第三方代码，`bac input record` 只表示“人类选择并提交了这些上下文”，不自动证明被粘贴材料由人类原创。

不要把以下内容直接写进 `.bac`：

- 密钥、token、cookie、私钥
- 完整私有 prompt
- 无关隐私数据
- 大段源码全文
- 不可公开的业务资料

优先记录摘要、hash、路径、命令、退出码、diff 摘要和验证结论。这样既能保留审计价值，又能减少敏感信息泄露风险。
