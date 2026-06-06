# BAC Stale Tail Repair Command Plan

**Goal:** 新增一个显式、受限、可审计的 `bac repair stale-tail` 命令，用于修复 `.bac` 账本尾部因旧 head、并发追加或 git 回退/合并造成的机械性分叉，同时防止该命令被用来重写贡献归因。

**Architecture:** 保持 `.bac` v2 ZIP 容器、事件格式、canonical JSON、hash chain 和 `human`、`ai`、`tool`、`system` 四类来源不变。新增 repair CLI 子命令和小型 repair service，只允许在可唯一证明为 stale-tail 的场景中改写 `prev_event_hash` 和由此必然变化的 `event_hash`。实际写入必须自动追加 repair 记录和本地 checkpoint。

**Tech Stack:** Python 3.10+，现有 BAC CLI，ZIP `.bac` 容器，canonical JSON，SHA-256，`pytest`。

**Minimal Change Scope:** 优先修改 `src/bac/adapters/cli.py`、新增或扩展 `src/bac/service/repair.py`、复用 `src/bac/storage/bac_file.py`、`src/bac/core/verify.py`、`src/bac/core/hash_chain.py`，并补充 `tests/test_bac_core.py`。按需更新 README、教程和 CHANGELOG。避免改变事件 schema、anchor 协议、签名策略和现有 `init/record/verify/inspect` 行为。

**Success Criteria:** `bac repair stale-tail --json` 默认只输出修复计划且不写文件；`--apply` 只在唯一、可证明、安全的尾部分叉场景中修复账本；修复不会改变 `source_type`、`actor`、`payload`、`evidence`、`summary` 等归因字段；修复后自动追加 repair 记录与 checkpoint；完整测试通过。

**Verification Plan:** 用临时 `.bac` 构造 stale-head 尾部分叉，验证 dry-run 输出计划但文件不变；执行 `--apply` 后验证账本无 errors、repair record 和 checkpoint 存在；构造内容篡改、中间断链、anchored/signed 尾部、多路径分叉等拒绝用例；运行 `python -m pytest -q`。

---

## 背景

本次排查 `/Volumes/2T01/Github/sub2api/docs/contribution.bac` 时，发现账本尾部出现典型 stale-head 分叉：

```text
event 69   hash = H69
event 70   prev = H69   hash = H70
event 71   prev = H69   hash = H71   # 应接到 H70
```

根因很可能是模型或工具基于旧 head 生成事件后，又被写入到已经前进的账本；也可能由 git 回退、手工合并 `.bac` 二进制容器或旧版本 BAC 工具追加造成。

当前已加固 `append_event`，新的旧 head 事件会被拒绝写入。但对于历史上已经损坏的账本，仍需要显式 repair 流程。这个流程应当帮助用户恢复机械性断链，而不能成为任意重写历史的工具。

## 威胁模型

`repair` 命令如果设计过宽，会削弱 `.bac` 的 tamper-evident 价值。主要风险包括：

- 攻击者篡改 `source_type`，把 AI 或 tool 贡献伪装成人类贡献，然后通过 repair 重新计算哈希链。
- 攻击者修改 `payload.summary`、`evidence` 或文件快照，再用 repair 让 `verify` 通过。
- 攻击者删除或重排不利事件，再用 repair 伪造连续尾部。
- 攻击者对已经远程 anchored 或 signed 的尾部做本地修复，掩盖与外部证据的冲突。
- 自动 repair 静默发生，导致用户不知道历史被改写。

因此 repair 命令必须是显式、窄范围、默认 dry-run、强留痕的审计工具。

## 安全边界

允许修改的字段只有：

- `prev_event_hash`
- `event_hash`

禁止修改的字段包括但不限于：

- `event_id`
- `event_type`
- `source_type`
- `trust_level`
- `created_at`
- `project`
- `actor`
- `payload`
- `evidence`
- `redactions`
- `signature`

实现时应在生成修复计划前后比较事件对象，确保除允许字段外没有任何差异。若需要修改其它字段才能通过验证，直接拒绝。

## 命令设计

建议命令：

```bash
bac repair stale-tail --json
bac repair stale-tail --json --apply
```

参数语义：

- `--json`：输出机器可读结果。
- `--apply`：实际写入；不带时只生成计划。
- `--max-events`：可选，限制一次最多修复的尾部事件数，默认较小，例如 8。
- `--no-checkpoint`：可选，默认不建议提供；如果提供，也应只用于测试或特殊迁移。

默认行为：

- 账本无错误时输出 `status=noop`。
- 账本错误不是 stale-tail 时输出 `status=refused`。
- 可修复但未传 `--apply` 时输出 `status=planned`。
- 成功写入后输出 `status=repaired`。

## Stale-Tail 判定

只接受尾部连续断链，不接受中间断链。

可修复模式：

```text
event N     prev = H(N-1), hash = HN
event N+1   prev = HN,     hash = HA
event N+2   prev = HN,     hash = HB
```

修复为：

```text
event N+2   prev = HA,     hash = recompute(event N+2)
```

如果尾部有多条连续 stale 事件，可以按事件序号逐条改接：

```text
event N+1   prev = HN
event N+2   prev = HN       -> 改为 H(N+1)
event N+3   prev = HN       -> 改为 H(N+2 repaired)
```

但必须能唯一判断顺序。默认使用容器事件序号作为唯一顺序，不根据 `created_at` 猜测。

## 拒绝场景

以下情况必须拒绝：

- 账本不是合法 v2 ZIP 容器。
- 存在重复 event entry、事件序号缺口或 manifest/genesis 不一致。
- 任意事件存在 `event_hash mismatch`，且 mismatch 不能完全由 planned `prev_event_hash` 改动解释。
- 断链不在尾部连续区域。
- 修复区域内包含 `trust_level=signed` 或 `trust_level=anchored` 的事件。
- 修复区域内包含远程 anchor receipt，或修复会改变已 anchor 的 head。
- 修复后仍有 schema、source policy、project root hash、human approval reference 等错误。
- 需要修改除 `prev_event_hash` 和 `event_hash` 之外的字段。
- 多条修复路径都可能成立，无法唯一判定。

## JSON 输出

dry-run 示例：

```json
{
  "status": "planned",
  "repair_type": "stale-tail",
  "apply": false,
  "bac_file": "docs/contribution.bac",
  "affected_events": [
    {
      "sequence": 71,
      "event_id": "bac_...",
      "old_prev_event_hash": "sha256:old",
      "new_prev_event_hash": "sha256:new",
      "old_event_hash": "sha256:oldhash",
      "new_event_hash": "sha256:newhash"
    }
  ],
  "refused": false,
  "warnings": []
}
```

拒绝示例：

```json
{
  "status": "refused",
  "repair_type": "stale-tail",
  "apply": false,
  "reason": "event 42 has event_hash mismatch unrelated to stale-tail repair",
  "errors": [
    "event bac_...: event_hash mismatch"
  ]
}
```

成功示例：

```json
{
  "status": "repaired",
  "repair_type": "stale-tail",
  "apply": true,
  "affected_events": [
    {
      "sequence": 71,
      "event_id": "bac_...",
      "old_event_hash": "sha256:oldhash",
      "new_event_hash": "sha256:newhash"
    }
  ],
  "repair_event_id": "bac_...",
  "checkpoint_event_id": "bac_...",
  "head_hash": "sha256:..."
}
```

## 写入策略

实际写入必须原子化：

- 先读取完整 ZIP 容器。
- 在内存中生成 repaired events。
- 写入同目录临时 `.bac` 文件。
- 完整验证临时文件。
- 原子替换原文件。
- 用标准 `append_event` 追加 repair record。
- 追加本地 checkpoint。
- 再次完整验证。

repair record 建议使用：

- `event_type=tool_command`
- `source_type=tool`
- `actor.declared_name=bac`
- `actor.declared_kind=system_tool`

summary 应包含受影响事件序号、旧 hash、新 hash 和拒绝任意归因字段变更的说明。

## 测试计划

### 规划但不写入

构造尾部分叉账本，执行：

```bash
bac repair stale-tail --json
```

断言：

- 返回 `status=planned`。
- 输出 affected event。
- 原文件 byte hash 不变。

### 应用修复

执行：

```bash
bac repair stale-tail --json --apply
```

断言：

- 返回 `status=repaired`。
- `bac verify --json` 无 errors。
- 原断链事件只改变 `prev_event_hash` 和 `event_hash`。
- 自动追加 repair record。
- 自动追加 checkpoint。

### 拒绝内容篡改

构造事件 `payload.summary` 被改但 `event_hash` 未更新的账本。

断言：

- repair 返回 `status=refused`。
- 不写文件。

### 拒绝归因篡改

构造 `source_type` 从 `ai` 改为 `human` 的账本。

断言：

- repair 返回 `status=refused`。
- 错误信息说明不是 stale-tail 机械修复。

### 拒绝中间断链

构造事件 10 断链，但事件 11 之后仍有正常或复杂历史。

断言：

- repair 返回 `status=refused`。

### 拒绝 signed / anchored 尾部

构造修复区域含 signed 或 anchored 事件。

断言：

- repair 返回 `status=refused`。

### CLI 回归

覆盖非 JSON 输出、`--json` 输出、非法参数、缺失 `.bac` 文件和无错误 noop。

## 文档更新

实现后需要同步更新：

- `README.md`
- `README.zh-CN.md`
- `docs/bac-tutorial.md`
- `CHANGELOG.md`

文档必须明确：

- repair 是 tamper-evident 辅助修复，不是不可篡改证明。
- repair 只修机械 stale-tail 断链。
- repair 不会也不能改变贡献归因字段。
- 已有远程 anchor 或签名冲突时不会自动修复。

## 非目标

- 不实现通用历史重写。
- 不自动合并任意两个 `.bac` 文件。
- 不修复被篡改的 payload/evidence。
- 不绕过 signature 或 remote anchor。
- 不把 `verify` 的所有错误都自动修掉。
- 不在普通 `bac record` 中静默 repair 历史。

## 执行顺序

### Task: 抽象 repair plan 数据结构

新增 `RepairPlan`、`RepairChange` 和拒绝原因结构，保证 CLI 与测试可以稳定读取。

### Task: 实现 stale-tail 检测

基于 `read_events` 和 `verify_events` 识别尾部连续 `prev_event_hash` mismatch，生成 dry-run 计划。

### Task: 实现安全校验

比较修复前后事件对象，只允许 `prev_event_hash` 和 `event_hash` 改变；检测 signed、anchored、anchor receipt 和非尾部断链。

### Task: 实现原子写入

新增内部写容器函数或 repair 专用写入函数，写临时文件并原子替换。

### Task: 接入 CLI

在 `bac repair stale-tail` 下提供 dry-run、`--apply` 和 `--json`。

### Task: 自动留痕

`--apply` 成功后追加 repair record 和 local checkpoint。

### Task: 补测试与文档

完成上述测试计划，更新 README、教程和 CHANGELOG。
