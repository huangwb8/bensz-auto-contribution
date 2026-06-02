# Human Input Attribution Underrecording Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 BAC 对 AI 编程会话中人类主动输入的系统性漏记，让用户在 Codex 等工具输入框中提交的需求、约束、审阅和授权能被低敏、可验证地记录为人类贡献。

**Architecture:** 保持 `.bac` v2 ZIP 容器、hash chain 和 `human`、`ai`、`tool`、`system` 四类来源不变。新增“人类输入事件”记录入口作为主路径，供 Codex wrapper、MCP/tool adapter 或其它 AI 工具宿主在收到用户消息时调用；保留 `Prompts.md`、聊天导出、issue/PR comment 等材料作为补充导入路径。完整 prompt 默认不入账，只记录摘要、消息 hash、来源通道、会话标识、分类、低敏摘录和必要证据。

**Tech Stack:** Python 3.10+，现有 BAC CLI，canonical JSON，SHA-256，ZIP `.bac` 容器，`pytest`，Markdown 文档。

**Minimal Change Scope:** 修改 `src/bac/adapters/cli.py`、`src/bac/service/event_builder.py`、`src/bac/service/evidence.py`、`src/bac/report/inspect.py`、`src/bac/core/verify.py`、`tests/test_bac_core.py`、`README.md`、`README.zh-CN.md`、`docs/bac-tutorial.md` 和 `CHANGELOG.md`。避免改变 `.bac` 容器格式、hash 计算、anchor 协议和服务端 API。

**Success Criteria:** AI 工具集成方可以在用户提交输入框消息时追加 `source_type=human` 事件；事件不保存完整私有 prompt；同一用户消息重复记录会被幂等跳过；`Prompts.md` 等 prompt log 可作为补充证据导入；`bac inspect --human` 能展示实时输入和导入输入；`bac verify` 能检查人类输入证据结构并提示潜在漏记风险；`python -m pytest -q` 通过。

**Verification Plan:** 运行 `python -m pytest -q`；在临时仓库模拟 Codex 用户消息记录，检查事件类型、消息 hash、低敏摘录和幂等；再模拟 `Prompts.md` 导入，确认它作为补充证据工作；执行 `bac inspect --human --json` 和 `bac verify --json` 确认人类输入贡献可见且验证无误。

---

## 背景判断

在 `/Volumes/2T01/Github/sub2api/docs/contribution.bac` 中，人类贡献事件明显偏少；而 `/Volumes/2T01/Github/sub2api/Prompts.md` 记录了大量用户发给 Codex 的需求、约束、审阅和操作策略。这个对照说明 BAC 当前漏记了人类输入。

但 `Prompts.md` 只是用户个人好习惯，不应成为系统正确性的前提。很多用户不会维护 prompt log，也可能把 prompt 写在 issue、PR comment、聊天导出、终端 wrapper、IDE 面板或其它地方。真正稳定的一手来源是：用户在 AI 工具输入框中主动提交的 user message。

因此计划的中心应从“导入 `Prompts.md`”调整为“记录 AI 工具接收到的用户消息”。`Prompts.md` 的价值是证明问题存在，并作为事后补录或交叉验证的补充证据。

## 根因

根因不是 `.bac` 容器、hash chain 或 verify 算法错误，而是采集入口不完整：

- 当前 `bac record` 只能记录调用方显式传入的事件，无法知道一条 AI 会话之前有哪些用户消息。
- BAC 没有为 Codex、Claude Code、MCP tool、IDE 插件或自定义 wrapper 定义“收到用户消息时应立即记录”的通用接口。
- 现有证据采集围绕文件快照、git diff 和命令结果，缺少用户消息 hash、来源通道、会话 ID、消息序号、低敏摘录和幂等键。
- 隐私边界强调不保存完整私有 prompt，这是正确的；但没有提供低敏替代记录，导致“不能保存完整 prompt”实际变成“多数 prompt 不记录”。
- 现有计划 `docs/plans/2026-06-01-source-laundering-defense.md` 已关注防止 AI 产物被漂白成人类贡献，但本计划要补上另一个方向：防止真实人类输入被系统漏记。
- `bac inspect --human` 只展示已记录的 `source_type=human` 事件，无法提示“当前账本可能没有接入用户输入采集”。

## 归因边界

用户输入框中的内容通常可以认定为人类主动行为，但只能记录为“人类提交了这条输入或上下文”，不能自动扩大为“输入内容中的每个片段都由人类原创”。

具体边界：

- 用户提出需求、约束、审阅意见、批准、否决、发布策略、风险偏好和业务判断，应记录为 `human_instruction`、`human_review` 或 `human_approval`。
- 用户粘贴日志、错误信息、网页内容、AI 生成文本或代码片段时，记录的是“人类选择并提交了这些上下文”，不把被粘贴材料本身改写为人类原创。
- 用户 prompt 的字数不等于贡献比例。短约束可能非常关键，长日志可能主要是证据提供。
- 人类批准 AI 产物时，仍应形成“AI 生成 + human_approval 指向被批准事件”的链式事实，不能把 AI 生成事件改成 `source_type=human`。

## 数据结构约定

不升级事件格式，先在 `payload` 和 `evidence` 中加入低敏输入来源字段。

实时用户输入事件示例：

```json
{
  "event_type": "human_instruction",
  "source_type": "human",
  "payload": {
    "summary": "用户要求调查 BAC 是否低估人类贡献并写优化计划",
    "input_provenance": {
      "format": "bac.human_input.v1",
      "channel": "ai_tool_user_message",
      "host": "codex",
      "session_id": "codex-session-id",
      "message_index": 12,
      "message_hash": "sha256:<normalized-user-message>",
      "recorded_full_text": false,
      "classification": "instruction"
    }
  },
  "evidence": [
    {
      "type": "human_input_message",
      "message_hash": "sha256:<normalized-user-message>",
      "redacted": true,
      "excerpt": "用户要求调查 BAC 是否低估人类贡献..."
    }
  ]
}
```

补充 prompt log 导入事件示例：

```json
{
  "event_type": "human_instruction",
  "source_type": "human",
  "payload": {
    "summary": "从 Prompts.md 导入用户历史 prompt 摘要",
    "input_provenance": {
      "format": "bac.human_input.v1",
      "channel": "prompt_log",
      "source_path": "Prompts.md",
      "start_line": 205,
      "end_line": 213,
      "message_hash": "sha256:<normalized-block>",
      "recorded_full_text": false,
      "classification": "instruction"
    }
  },
  "evidence": [
    {
      "type": "prompt_log_block",
      "source_path": "Prompts.md",
      "start_line": 205,
      "end_line": 213,
      "message_hash": "sha256:<normalized-block>",
      "redacted": true
    }
  ]
}
```

## 解决方案

主路径：新增低敏实时输入记录命令，供 AI 工具宿主调用。

建议命令：

```bash
bac input record \
  --channel ai_tool_user_message \
  --host codex \
  --session-id "$CODEX_SESSION_ID" \
  --message-index 12 \
  --message-file /tmp/user-message.txt
```

命令行为：

- 从 `--message-file` 或 stdin 读取用户消息。
- 规范化消息文本后计算 `message_hash`。
- 自动脱敏并生成短摘要和低敏摘录。
- 根据内容分类为 `human_instruction`、`human_review` 或 `human_approval`；分类不确定时保守使用 `human_instruction`。
- 使用 `channel + host + session_id + message_index + message_hash` 作为幂等键。
- 默认不保存完整消息；如未来允许保存完整文本，必须显式参数开启，并经过 redaction。

补充路径：保留 prompt log 导入命令。

建议命令：

```bash
bac input import-log \
  --source-file Prompts.md
```

导入行为：

- 支持 Markdown fenced code block、`---` 分隔块和常见聊天导出片段。
- 每个候选区块生成同一套 `input_provenance` 和 `message_hash`。
- 重复导入时根据 `message_hash` 和来源位置跳过。
- `Prompts.md` 不存在时不报错。

审计路径：增强 inspect 和 verify。

- `bac inspect --human` 展示人类输入来源：`ai_tool_user_message`、`prompt_log`、`issue_comment` 等。
- `bac verify` 校验 `input_provenance.format`、`channel`、`message_hash`、`recorded_full_text` 和 evidence 结构。
- 若账本包含大量 AI 事件但没有任何 `human_input_message` 或 `prompt_log_block` 证据，给 warning：`ledger has AI activity but no human input provenance; human contributions may be underrecorded`。

## 非目标

- 不保存完整私有 prompt。
- 不从字数、消息数量或 prompt 长度推导最终贡献比例。
- 不证明现实世界中某段粘贴文本一定由用户原创。
- 不把 AI 根据人类 prompt 实现的代码改记为 `human`。
- 不强制所有项目都维护 `Prompts.md`。
- 不修改 Codex 本体；本项目只提供 BAC CLI/API 和集成协议，Codex wrapper 或其它宿主负责调用。

## 执行任务

### Task 1: 为实时用户输入定义失败测试

**Files:**
- Modify: `tests/test_bac_core.py`

**Step 1: 添加 CLI 测试**

创建临时项目并初始化 BAC，模拟一条 Codex 用户消息：

```bash
python -m bac --root "$tmpdir" input record \
  --channel ai_tool_user_message \
  --host codex \
  --session-id s1 \
  --message-index 1 \
  --message-file "$tmpdir/user-message.txt" \
  --json
```

断言：

- 追加一条 `source_type=human` 事件。
- `payload.input_provenance.channel` 为 `ai_tool_user_message`。
- evidence 包含 `type=human_input_message`。
- 事件含 `message_hash`，但不含完整消息原文。

**Step 2: 添加幂等测试**

重复执行同一命令，断言不会重复追加事件，JSON 输出包含 skipped 计数。

**Step 3: 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_bac_core.py -q
```

Expected: FAIL，因为还没有 `input record` 子命令。

### Task 2: 实现人类输入证据构造

**Files:**
- Modify: `src/bac/service/evidence.py`
- Modify: `src/bac/service/event_builder.py`

**Step 1: 新增消息规范化与 hash**

实现 `normalize_human_input_message(text: str) -> str` 和 `build_human_input_evidence(...)`：

- 统一换行。
- 去掉首尾空白。
- 保留语义内容。
- 对规范化文本计算 SHA-256。

**Step 2: 新增低敏摘要与摘录**

复用现有 redaction 逻辑，默认生成短摘要和脱敏 excerpt，不把完整文本写入 payload 或 evidence。

**Step 3: 新增分类函数**

实现轻量分类：

- 包含“可以吗”“你觉得呢”“审查”“review”倾向 `human_review`。
- 包含“批准”“确认”“发布”“release”且表达授权时倾向 `human_approval`。
- 其它默认 `human_instruction`。

**Step 4: 运行聚焦测试**

Run:

```bash
python -m pytest tests/test_bac_core.py -q
```

Expected: 服务层测试通过，CLI 测试仍失败。

### Task 3: 新增 `bac input record` CLI

**Files:**
- Modify: `src/bac/adapters/cli.py`
- Modify: `tests/test_bac_core.py`

**Step 1: 添加子命令**

新增：

```bash
bac input record
```

参数：

- `--channel`，默认 `ai_tool_user_message`。
- `--host`，例如 `codex`、`claude-code`、`mcp-wrapper`。
- `--session-id`。
- `--message-index`。
- `--message-file`，省略时从 stdin 读取。
- `--classification`，允许调用方显式指定 `instruction`、`review`、`approval`。
- `--json`。

**Step 2: 实现幂等**

读取现有事件中的 `payload.input_provenance` 和 evidence message hash。若同一 `channel + host + session_id + message_index + message_hash` 已存在，则跳过。

**Step 3: 运行测试**

Run:

```bash
python -m pytest tests/test_bac_core.py -q
```

Expected: `input record` 测试通过。

### Task 4: 新增 `bac input import-log` 补充导入

**Files:**
- Modify: `src/bac/adapters/cli.py`
- Modify: `src/bac/service/evidence.py`
- Modify: `tests/test_bac_core.py`

**Step 1: 添加 prompt log 解析测试**

用临时 `Prompts.md` 覆盖：

- fenced code block。
- `---` 分隔块。
- 普通标题下 prompt。
- 含伪 token 的敏感文本。

**Step 2: 实现导入**

新增：

```bash
bac input import-log --source-file Prompts.md
```

每个区块复用 `build_human_input_evidence`，但 `channel` 固定为 `prompt_log`，并记录 `source_path`、`start_line`、`end_line`。

**Step 3: 运行测试**

Run:

```bash
python -m pytest tests/test_bac_core.py -q
```

Expected: prompt log 作为补充证据导入成功，重复导入幂等。

### Task 5: 增强 inspect 与 verify

**Files:**
- Modify: `src/bac/report/inspect.py`
- Modify: `src/bac/core/verify.py`
- Modify: `tests/test_bac_core.py`

**Step 1: inspect 展示来源**

JSON 输出中增加 `input_provenance` 摘要，包括 `channel`、`host`、`source_path`、`start_line`、`end_line` 和 `classification`。

**Step 2: verify 检查结构**

校验：

- `input_provenance.format == "bac.human_input.v1"`。
- `channel` 非空。
- `message_hash` 是合法 SHA-256。
- `recorded_full_text` 为布尔值。
- `human_input_message` 或 `prompt_log_block` evidence 至少存在一个。

**Step 3: 添加漏记 warning**

若账本中存在 `ai_plan`、`ai_generation` 或 AI `file_change`，但没有任何 `input_provenance`，输出 warning：

```text
ledger has AI activity but no human input provenance; human contributions may be underrecorded
```

**Step 4: 运行测试**

Run:

```bash
python -m pytest tests/test_bac_core.py -q
```

Expected: inspect/verify 新增测试通过。

### Task 6: 更新文档与变更记录

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/bac-tutorial.md`
- Modify: `CHANGELOG.md`

**Step 1: 文档说明新主路径**

补充：

- AI 工具宿主应在收到用户输入时调用 `bac input record`。
- `Prompts.md` 等日志只是补充导入证据，不是系统正确性的前提。
- 默认不保存完整 prompt，只保存低敏证据。
- 用户粘贴内容记录为“人类提交上下文”，不自动证明粘贴内容原创。

**Step 2: 添加示例**

```bash
bac input record --host codex --session-id s1 --message-index 1 --message-file /tmp/user-message.txt
bac input import-log --source-file Prompts.md
bac inspect --human
```

**Step 3: 更新 CHANGELOG**

在 `[Unreleased]` 记录新增人类输入记录接口、prompt log 补充导入和漏记 warning。

### Task 7: 全量验证与手动模拟

**Files:**
- No direct edits.

**Step 1: 运行全量测试**

Run:

```bash
python -m pytest -q
```

Expected: PASS。

**Step 2: 手动模拟 Codex 主路径**

Run:

```bash
tmpdir=$(mktemp -d)
printf '%s\n' '请调查 BAC 是否低估人类贡献，并写优化计划。' > "$tmpdir/user-message.txt"
python -m bac --root "$tmpdir" init --mode local
python -m bac --root "$tmpdir" input record --host codex --session-id s1 --message-index 1 --message-file "$tmpdir/user-message.txt" --json
python -m bac --root "$tmpdir" inspect --human --json
python -m bac --root "$tmpdir" verify --json
```

Expected: 生成 1 条 human input 事件，inspect 可见，verify 通过。

**Step 3: 手动模拟 prompt log 补充路径**

Run:

```bash
cp /Volumes/2T01/Github/sub2api/Prompts.md "$tmpdir/Prompts.md"
python -m bac --root "$tmpdir" input import-log --source-file Prompts.md --json
python -m bac --root "$tmpdir" inspect --human --json
python -m bac --root "$tmpdir" verify --json
```

Expected: 导入多条补充 human input 事件；重复执行不新增事件。

## 审查清单

- 用户输入框消息记录为 `source_type=human`，但不把粘贴内容自动认定为人类原创。
- AI 计划、AI 生成和 AI 驱动文件修改仍必须是 `source_type=ai`。
- 人类批准 AI 产物时使用 `human_approval` 指向 AI 事件，不改写 AI 事件来源。
- 默认不保存完整 prompt。
- `Prompts.md` 缺失时不报错。
- 旧账本没有输入来源证据时最多 warning，不破坏兼容性。
- 文档必须明确 BAC 是 tamper-evident 的过程记录，不是最终贡献比例裁判。

## 回滚方案

如果实时输入记录接口的隐私策略或分类规则需要调整，保留已有 `.bac` 事件合法性，只暂停新 CLI 子命令或将 verify 漏记提示降级为文档说明。已记录事件仍可通过 hash chain 验证，并继续被 `inspect --human` 展示。
