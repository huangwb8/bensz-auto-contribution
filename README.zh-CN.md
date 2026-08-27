<div align="center">

# 🧭 Bensz Auto Contribution

**面向人类—AI 软件协作的篡改可发现贡献记录器**

[![Release](https://img.shields.io/github/v/tag/huangwb8/bensz-auto-contribution?label=release&color=blue)](https://github.com/huangwb8/bensz-auto-contribution/tags)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![BAC Format](https://img.shields.io/badge/BAC_format-v2-7C3AED.svg)](docs/bac-tutorial.md)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | [中文](README.zh-CN.md)

</div>

---

## BAC 是什么？

用 AI 编程工具做项目时，最终 diff 只能告诉我们“哪些行变了”，通常不能告诉我们“这些变化是怎样产生的”。需求可能来自人类，实现方案可能由 AI 提出，测试结果则来自工具。如果最后只剩一句“作者修改了这些代码”，协作过程里最有价值的上下文就丢失了。

**Bensz Auto Contribution（简称 BAC）** 是一个轻量命令行工具。它把这些上下文按发生顺序写进项目绑定的 `.bac` 文件：人类提出了什么、AI 生成或建议了什么、工具观察到了什么、文件实际变成了什么，以及人类何时审核或批准了结果。

可以把它理解成开发过程的“黑匣子”。它帮助团队在事后讲清楚过程、复核协作边界、发现记录被改动的痕迹；它不是作者裁判，也不替代机构或项目规则。

### 一分钟理解它的工作方式

```text
人类需求 → AI 方案/代码 → 文件变更 → 工具/测试证据 → 人类批准
```

每个箭头都会变成一条追加式事件。事件通过 hash 指向前一条记录，因此修改、删除或调换历史顺序会破坏链条。BAC 区分四类直接来源：

| 来源 | 含义 | 常见例子 |
| --- | --- | --- |
| `human` | 人类提供了意图或做出了决定 | 需求、约束、审阅、手写修改、批准 |
| `ai` | AI 生成或提出了工作 | 方案、代码草稿、重构、修复 |
| `tool` | 程序产生了可观察结果 | 命令输出、测试结果、diff 摘要 |
| `system` | BAC 自己记录的账本事件 | genesis、checkpoint、verification |

这里的来源表示“这条记录从哪里来”，不是把所有功劳粗暴地归给某一方。

## BAC 的理念

- **记录过程，不事后编故事。** 尽量在意图和证据产生时记录。
- **批准不等于创作。** 人类接受 AI 代码时，保留 `ai_generation`，再追加独立的 `human_approval`。
- **证据优先于声明。** 文件 hash、diff、命令退出码和测试结果，比未经核验的“已完成”更有用。
- **默认保护隐私。** 人类输入默认只保存脱敏摘要和带域分离的 hash，不写入完整私有 prompt 或密钥。
- **诚实描述安全性。** BAC 是 tamper-evident（篡改可发现），不是 tamper-proof（绝对防篡改）。有写权限的人仍可能重写本地文件，但这种重写更容易被验证器识别；可选的签名远程 receipt 能进一步检查尾部截断。
- **人类始终在回路中。** 账本支持复核和争议重建，但不替代项目政策、机构规则或人的判断。

## 它适合什么场景？

只要一个成果由人类和 AI 共同完成，BAC 就能帮助保留过程上下文：

- **软件开发：** 把需求、生成代码、文件变更和测试证据串起来。
- **科研与写作：** 区分草稿、人类修改、工具检查和最终批准。
- **团队协作：** 复原谁做了什么决定、哪些内容由 AI 提出、哪些结果确实被工具观察到。

无论在哪种场景，规则都一样：记录每一步的来源和证据，不要从最终文档倒推所有权。

## 五分钟快速开始

### 环境与安装

- Python 3.10+
- 运行时无第三方依赖

```bash
python -m pip install bensz-auto-contribution
# 或在源码目录中：
python -m pip install -e .
```

### 初始化并记录一次协作

在目标项目根目录执行。默认账本是 `docs/contribution.bac`；如果 `docs/` 不存在，`bac init` 会自动创建。

```bash
# 1. 创建与项目绑定的 .bac 容器
bac init

# 2. 记录人类需求（AI tool 宿主收到消息时应立即执行）
bac input record \
  --host codex \
  --session-id s1 \
  --message-index 1 \
  --message-file /tmp/user-message.txt

# 3. 分开记录 AI 工作和工具观察
bac record --event-type ai_generation --source-type ai \
  --summary "实现哈希链验证器"
bac record --event-type test_result --source-type tool \
  --summary "单元测试通过" \
  --command-text "python -m pytest -q" --exit-code 0

# 4. 验证并查看时间线
bac verify
bac inspect
```

对于 AI tool 集成，推荐在宿主收到用户消息的时刻调用 `bac input record`。它记录低敏的人类输入 provenance，而不是完整 prompt。`Prompts.md` 导入适合历史补录或交叉核验，但不应成为系统正确性的唯一来源。

所有命令都支持 `--root`（指定目标项目）和 `--bac-file`（指定自定义账本路径）。`init`、`record`、`input`、`verify`、`repair`、`inspect` 还支持 `--json`，便于 AI tool 和自动化流程调用。

## `.bac` 文件里有什么？

默认文件是一个基于 ZIP 的 v2 容器，但用户通常只需维护这一个文件：

```text
docs/contribution.bac
├── manifest.json
└── events/
    ├── 000000000001.json
    └── 000000000002.json
```

`manifest.json` 保存项目绑定和存储约定；每条事件是 canonical JSON，包含：

- 事件类型、直接来源、信任等级和时间戳；
- 项目上下文（根目录绑定、git commit/branch、工作区状态）；
- 描述工作内容的 payload（摘要、命令或文件快照）；
- 文件 hash、diff 摘要、退出码、测试结果等 evidence；
- `prev_event_hash` 与 `event_hash`，共同形成可验证哈希链。

验证器会检查 ZIP 结构、重复路径、事件编号连续性、manifest/genesis 一致性、来源语义和重算后的哈希链。字段级示例见 [BAC 工作原理教程](docs/bac-tutorial.md)。

## 常见工作流

### 查看人类贡献

```bash
bac inspect --human
bac inspect --source-type human --since 2026-05-01 --until 2026-05-31 --json
```

只写日期时按 UTC 自然日解释；需要精确边界时传 ISO-8601 时间戳。人类批准 AI 工作时，应追加指向前序 AI 事件的 `human_approval`；BAC 不会因此把 AI 生成改写成人类创作。

### Checkpoint、修复与并发写入

```bash
bac record --event-type checkpoint --source-type system --summary "记录当前 head hash"
bac repair stale-tail --json              # 只生成计划
bac repair stale-tail --json --apply      # 审阅计划后再应用
```

日常写入使用单账本 OS 锁，在锁内读取最新 head、重建临时容器、验证后原子替换，保护并发 `bac record`。`repair stale-tail` 是严格受限的维护命令：只允许修复机械性尾部分叉，拒绝内容/归因修改、signed 或 anchored 事件、checkpoint 以及非尾部断链。

### 可选的隐私保护锚定

`bac init` 默认是本地优先的 `hybrid` 模式。请求只发送盲化 `anchor_hash`（可选低敏 client summary），不上传 `.bac` 内容、路径、diff、prompt、actor、项目名或原始 head hash。

```bash
bac anchor request --json
bac anchor import --receipt-file receipt.json --public-key "$ANCHOR_PUBLIC_KEY"
bac verify --require-anchor
```

自托管 reference server：

```bash
docker compose -f server/docker-compose.yml up --build
```

`bac cloud register/login/link/status` 提供可选的面向用户流程。token 保存在本机用户配置中，不写入 `.bac`；生产锚定写入要求安全 HTTPS 和 token。

## 安全边界：它能证明什么，不能证明什么？

BAC 能发现事件内容被编辑、事件缺失或重排、重复 ZIP 成员、哈希链断裂、checkpoint 不一致和常见的来源漂白模式。它不能保证现实中的每个动作都被记录，也不能阻止有写权限的人重写账本，更不能单独裁定作者、版权或责任。远程签名 receipt 只能证明某个盲化 head 在服务端时间点存在，不能证明历史记录完整无缺。

因此，`.bac` 应被理解为**带有明确边界、可复核的过程证据**，而不是不可质疑的真相来源。

## 新用户常问

**需要记录每一次按键吗？** 不需要。记录有意义的阶段即可：意图、AI 工作、文件证据、工具结果和批准。AI tool 宿主可以自动记录最早的人类输入事件。

**初始化前发生的工作还能还原吗？** 只能依赖手头仍保留的证据。prompt log 或导入的笔记可以帮助补录，但事后记录天然弱于当时记录。

**`.bac` 是作者证明或法律凭证吗？** 不是。它是可复核的过程记录；作者归属和责任仍由机构、期刊、合同和相关人员决定。

**验证失败怎么办？** 先阅读验证报告。如果只是机械性尾部分叉，可用 `bac repair stale-tail` 生成受限的 dry-run 计划；涉及内容或归因重写时，命令会拒绝执行。

## 开发与测试

```bash
python -m pytest -q
python -m unittest discover -s tests -v
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

测试覆盖 canonicalization、v2 容器、哈希链、篡改检测、脱敏、checkpoint、私有 anchor receipt、服务端流程和 CLI 端到端行为。发布流程见 [PyPI 发布](docs/pypi-release.md) 与 [DockerHub 发布](docs/dockerhub-release.md)。

## 仓库导航

```text
src/bac/       CLI、事件模型、存储、验证、锚定、报告
tests/         客户端与端到端测试
server/        可选的 reference anchor server
docs/          BAC 教程、发布指南和计划
```

## 参与贡献

欢迎围绕 `.bac` 格式、威胁模型、AI tool 适配器、验证逻辑、签名/时间戳和开发者体验提交 issue 或 pull request。修改归因逻辑时请保持边界准确：BAC 记录过程与证据，不宣称不可修改的最终归属。

## 许可证

MIT License
