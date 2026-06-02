# 贡献来源漂白防护 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 防止用户诱导 AI 或工具把 AI 实际贡献伪装为人类贡献，确保 BAC 记录按事实区分创作来源、操作来源、验证证据与人工批准。

**Architecture:** 保持现有 v2 事件模型和 `human`、`ai`、`tool`、`system` 四类来源不变，在事件语义层增加 attribution policy 与 validator 规则。实现重点是把 `human_approval`、`human_review`、`human_instruction` 与 `ai_generation`、`file_change` 的关系记录清楚，而不是引入复杂权限系统。

**Tech Stack:** Python 3.10+，现有 BAC v2 ZIP container，canonical JSON，SHA-256 hash chain，`unittest`/`pytest`，Markdown 文档。

**Minimal Change Scope:** 允许修改 `src/bac/core/schema.py`、`src/bac/core/verify.py`、`src/bac/service/event_builder.py`、`src/bac/adapters/cli.py`、`tests/test_bac_core.py`、`README.md`、`README.zh-CN.md`、`docs/bac-tutorial.md`、`CHANGELOG.md`。避免修改 anchor server、容器格式、哈希算法、远程 receipt 协议和发布配置。

**Success Criteria:** 文档明确将贡献来源漂白列为威胁；CLI 与 validator 能拒绝或警告明显的来源/事件类型矛盾；人类批准 AI 产物可以被记录为 `human_approval`，但不能把 AI 生成或 AI 文件修改记录为人类创作；新增测试覆盖误标、批准关系和兼容性边界；`python -m pytest -q` 通过。

**Verification Plan:** 运行 `python -m pytest -q`；手动创建包含 `human_approval` 指向 `ai_generation` 的账本并验证通过；手动创建 `event_type=ai_generation, source_type=human` 和 `event_type=human_approval, source_type=ai` 的账本并验证失败或给出明确错误；检查 README 和教程不把签名或批准表述为创作证明。

---

## 背景判断

当前项目已经具备四类来源、事件类型、hash chain、checkpoint、receipt 和签名预留字段，但对“来源漂白”仍有三个明显缺口：

- 威胁模型没有显式说明：用户可能要求 AI 把 AI 生成内容记成人类贡献。
- 事件语义没有约束：`event_type` 与 `source_type` 的组合目前只校验枚举值，不校验含义是否冲突。
- 人类批准与创作来源没有结构化区分：可以记录 `human_approval`，但缺少指向被批准事件的标准字段与验证规则。

## 非目标

- 不试图证明现实世界中某段代码一定由谁亲手输入。
- 不把 BAC 扩展为最终署名、法律归属或责任裁判系统。
- 不实现完整事件签名系统。
- 不改变现有 `.bac` v2 容器格式和 hash chain 计算方式。

## 威胁模型

攻击名称：贡献来源漂白攻击。

攻击路径：

- 用户要求 AI 将 AI 生成的方案、代码或自动修复记录为 `human`。
- AI 为了服从用户指令，将 `ai_generation`、AI 驱动的 `file_change` 或工具生成结果误记为 `human`。
- 后续审计方使用 `bac inspect --human` 时看到虚高的人类贡献。

安全目标：

- BAC 按事实记录直接来源，不按署名偏好记录。
- 人类可以记录需求、约束、审阅、批准和最终授权。
- AI 产物被人类采纳时，应形成“AI 生成 + 人类批准”的链式事实，而不是改写为“人类创作”。

## 数据结构约定

在不升级事件格式的前提下，先使用 `payload` 承载关系字段：

```json
{
  "event_type": "human_approval",
  "source_type": "human",
  "payload": {
    "summary": "Human approved AI-generated implementation",
    "approves_event_hash": "sha256:<ai-event-hash>",
    "approval_scope": "accept_for_merge"
  }
}
```

字段语义：

- `approves_event_hash`：被人类批准或采纳的既有事件 hash。
- `approval_scope`：批准范围，例如 `accept_for_merge`、`reviewed_with_changes`、`acknowledged_ai_use`。
- `source_type` 仍表示这条批准记录的直接来源是人类，不改变被批准事件的创作来源。

## 事件语义规则

新增一组轻量规则，先在验证器中实现：

- `human_instruction`、`human_review`、`human_approval` 的 `source_type` 必须是 `human`。
- `ai_plan`、`ai_generation` 的 `source_type` 必须是 `ai`。
- `tool_command`、`test_result` 的 `source_type` 必须是 `tool`。
- `genesis`、`checkpoint`、`verification` 的 `source_type` 必须是 `system`，除非现有兼容性测试证明需要保留旧行为。
- `human_approval` 如包含 `approves_event_hash`，该 hash 必须指向前序事件，不能指向未来事件或自身。
- `human_approval` 不得把被批准事件的 `source_type` 改写为 `human`；验证报告应保留两条独立事实。

兼容策略：

- 对明显矛盾的新账本记录返回 fail。
- 如果担心旧账本已有宽松组合，可先在首个版本将部分规则设为 warning，并在 README 中声明后续版本会收紧为 fail。
- 对 `file_change` 保持谨慎：它既可能是人手编辑，也可能是 AI 驱动或工具生成，暂不强制唯一 `source_type`，但要求文档说明应按实际直接来源填写。

## 任务：补充文档威胁模型

**Files:**
- Modify: `README.zh-CN.md`
- Modify: `README.md`
- Modify: `docs/bac-tutorial.md`
- Modify: `CHANGELOG.md`

**Steps:**

- 在安全模型中新增“贡献来源漂白攻击”说明。
- 明确“批准/授权不等于创作来源”。
- 在 `.bac` 字段说明中补充 `human_approval.payload.approves_event_hash` 示例。
- 在 CHANGELOG 的 `[Unreleased]` 记录安全边界与计划中的验证器加固。

**Verification:**

Run:

```bash
rg -n "source laundering|贡献来源漂白|批准.*创作|approval" README.md README.zh-CN.md docs/bac-tutorial.md CHANGELOG.md
```

Expected: 中英文 README 和教程都能检索到清晰边界说明。

## 任务：实现事件语义策略

**Files:**
- Modify: `src/bac/core/schema.py`
- Modify: `src/bac/core/verify.py`

**Steps:**

- 在 schema 层新增事件类型到允许来源的映射。
- 在 verifier 中对每条事件调用语义校验。
- 为 `human_approval.payload.approves_event_hash` 建立前序 hash 索引，拒绝未来引用、自引用和不存在引用。
- 错误消息使用可审计措辞，例如 `event <id>: ai_generation must use source_type ai`。

**Verification:**

Run:

```bash
python -m pytest tests/test_bac_core.py -q
```

Expected: 新增语义测试通过，既有 hash chain、checkpoint、anchor 测试不回退。

## 任务：加固记录入口

**Files:**
- Modify: `src/bac/service/event_builder.py`
- Modify: `src/bac/adapters/cli.py`

**Steps:**

- 在 builder 输入校验中复用事件语义策略，尽早拒绝明显矛盾的 `event_type/source_type` 组合。
- 在 CLI error 中提示正确替代方式：将 AI 产物记为 `ai_generation`，再追加 `human_approval`。
- 支持通过 `--payload-json` 写入 `approves_event_hash`，无需新增 CLI 参数，保持接口简洁。

**Verification:**

Run:

```bash
bac record --event-type ai_generation --source-type human --summary "Misattributed AI output"
```

Expected: 命令失败，并提示该事件应使用 `source_type=ai`。

## 任务：补充测试覆盖

**Files:**
- Modify: `tests/test_bac_core.py`

**Steps:**

- 添加测试：`ai_generation + human` 被 verifier 拒绝。
- 添加测试：`human_approval + ai` 被 verifier 拒绝。
- 添加测试：`human_approval` 指向前序 `ai_generation` 时验证通过。
- 添加测试：`human_approval` 指向不存在 hash 时验证失败。
- 添加测试：builder 拒绝明显矛盾的组合。

**Verification:**

Run:

```bash
python -m pytest -q
```

Expected: 全量测试通过。

## 审查清单

- `bac inspect --human` 只会展示真正来源为人类的需求、审阅、批准和人工修改，不把 AI 生成事件混入。
- 文档没有把 `human_approval`、签名或 receipt 描述为创作证明。
- 错误消息说明如何正确记录“人类采纳 AI 产物”。
- 所有新增规则都保留 BAC 的定位：tamper-evident process record，而不是不可篡改或最终归属裁判。

## 回滚方案

如果事件语义收紧导致旧账本大量失败，先把 verifier 中的新增错误降级为 warning，并保留 builder/CLI 对新记录的 fail-fast。文档仍保留来源漂白威胁说明，因为这是产品安全边界，不应回滚。
