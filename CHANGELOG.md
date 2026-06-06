# Changelog

本文件记录项目重要变更，格式遵循 Keep a Changelog，并优先维护 `[Unreleased]`。

## [Unreleased]

## [1.3.0] - 2026-06-07

### Fixed（修复）

- 加固 BAC v2 容器写入：追加事件不再使用 ZIP append 原地修改，而是在账本锁内重建同目录临时容器、校验通过后原子替换，降低多 agent 并发、进程中断或写入失败导致 ZIP central directory 尾部损坏的风险。
- `repair stale-tail` 复用统一的原子容器重写路径，避免 repair 写入与日常 append 写入的可靠性策略漂移。

## [1.2.7] - 2026-06-07

### Fixed（修复）

- 加固日常并发写入：普通 `bac record`、`bac input record`、`bac input import-log` 和 `bac config set` 会在账本锁内完成读取当前 head、构造事件和追加写入，避免两个进程基于同一旧 head 竞争导致第二条记录丢失或生成 stale-tail。
- `append_event` 新增同目录文件锁，并提供显式 `allow_stale_head_rebase` 选项；该选项只允许普通未签名、未锚定、非 checkpoint 事件自动重接到最新 head，且只改变 `prev_event_hash` 与派生 `event_hash`。

### Changed（变更）

- 更新中英文 README 与 `docs/bac-tutorial.md`：补充账本锁串行化写入说明，明确日常并发应优先依赖锁定写入路径，`repair stale-tail` 主要用于历史账本修复。

## [1.2.6] - 2026-06-06

### Added（新增）

- 新增 `bac repair stale-tail` 命令：默认 dry-run 输出可审计修复计划，`--apply` 仅在可唯一证明为机械性旧 head 尾部分叉时重接尾部 `prev_event_hash` 并重算派生 `event_hash`。
- 新增 `src/bac/service/repair.py` 模块：实现受限尾部修复逻辑，包括分叉检测、计划生成、原地修复写入、repair record 追加和本地 checkpoint。
- 修复应用后自动追加 `tool_command/source_type=tool` repair record 与本地 checkpoint，保留修复行为本身的审计证据。
- 新增 `bac repair stale-tail` 相关 CLI 入口、`--max-events`、`--apply`、`--json` 参数。
- 新增计划文档 `docs/plans/2026-06-06-stale-tail-repair-command.md`。

### Changed（变更）

- 更新中英文 README 与 `docs/bac-tutorial.md`：补充 `bac repair stale-tail` 用法说明和安全边界描述。

### Fixed（修复）

- 为历史上已存在的 stale-head 尾部分叉提供受限修复路径，同时拒绝内容篡改、归因字段变化、signed/anchored/checkpointed 尾部事件和非尾部断链，避免 repair 被用作贡献来源重写工具。

## [1.2.5] - 2026-06-06

### Fixed（修复）

- 加固 BAC 追加写入：`append_event` 现在会拒绝 `prev_event_hash` 不等于当前账本 head 的事件，避免并发、旧 head 或 git 回退合并场景生成分叉尾部。
- 修正验证事件来源策略：`verification` 事件允许由 `tool` 或 `system` 记录，避免工具执行的验证证据被误判为来源矛盾。

## [1.2.4] - 2026-06-02

### Added（新增）

- 新增人类输入记录主路径：`bac input record` 可在 AI tool 宿主收到用户消息时追加低敏 `source_type=human` 事件，记录 provenance、消息 hash、分类、脱敏摘要和 evidence，并支持幂等跳过重复消息。
- 新增 prompt log 补充导入：`bac input import-log --source-file Prompts.md` 可从项目内 Markdown prompt log 导入脱敏人类输入证据，重复导入会跳过已记录区块。
- `bac inspect --human --json` 新增 `input_provenance` 摘要输出，便于审计实时输入与补充导入来源。
- 新增贡献来源漂白防护说明：中英文 README 与教程明确“批准不等于创作来源”，并补充 `human_approval.payload.approves_event_hash` 示例。

### Changed（变更）

- 整理 BAC Anchor 部署目录职责：`docs/deploy` 仅保留可复制到服务器的 Compose 配置、环境变量示例和部署说明，部署/日志/备份/恢复辅助脚本迁移到 `tools/`。
- 扩展敏感信息脱敏规则，覆盖更多 token、JWT、URL query 凭证、邮箱和中文密钥字段，并对人类输入消息 hash 做 BAC 域分离。

### Fixed（修复）

- 修复 AI 编程会话中人类主动输入系统性漏记的问题：`bac verify` 会校验人类输入 provenance/evidence 结构，并在账本存在 AI 活动但没有人类输入 provenance 时给出 underrecording warning。
- 加固 BAC 事件归因语义验证：拒绝 `ai_generation/source_type=human`、`human_approval/source_type=ai` 等明显来源矛盾，校验 `human_approval.payload.approves_event_hash` 只能指向同一账本前序事件，并让 CLI 在写入前拒绝无效批准引用。

## [1.2.3] - 2026-05-31

### Added（新增）

- 新增 BAC Cloud 账号与项目账本绑定流程：服务端支持用户注册、登录、用户 token、cloud ledger 创建和 Web 登录页；CLI 新增 `bac cloud register/login/link/status`。
- 新增云端自动锚定：`bac cloud link` 会把本地 `.bac` 配置为 `hybrid`、启用 `anchor.require` 与 `cloud.auto_anchor`，后续 `bac record` 会自动向已绑定服务端提交盲化 anchor request 并写回 signed checkpoint。
- Anchor request 新增可选 `client_summary`，仅上传事件数量、来源/信任等级计数和当前 head 事件类型等低敏摘要，避免上传路径、diff、payload、prompt、actor 或原始 `head_hash`。

## [1.2.2] - 2026-05-31

### Added（新增）

- 新增 `bac inspect` 贡献提取过滤能力：支持 `--human` 快速提取人类贡献，并支持 `--source-type`、`--since`、`--until`、`--on` 按来源和 UTC 日期/时间范围筛选 `.bac` 事件。
- 新增 `docs/deploy` 服务器部署包：提供 BAC Anchor 的 Docker Compose、`.env.example`、部署/日志/备份/恢复脚本，默认使用 `npm_default` 网络和 `bac-anchor-app`、`bac-anchor-postgres`、`bac-anchor-redis` 容器命名。

### Changed（变更）

- 扩展 BAC Anchor Server 生产依赖：支持 PostgreSQL 作为持久化数据库，并在配置 `BAC_ANCHOR_REDIS_URL` 时使用 Redis 保存生产限流状态。
- 更新 PyPI 发布说明：补充基于本地 PyPI 配置的 twine 直传流程，覆盖不经过 GitHub Actions 的发布场景。

## [1.2.1] - 2026-05-31

### Added（新增）

- 新增 DockerHub 本地直推发布流程：提供 `tools/dockerhub-publish.sh` 与 `make dockerhub-publish`，仅构建并发布 `linux/amd64` 镜像，不经过 GitHub Actions；同步补充发布文档、README 入口和服务端说明。

### Changed（变更）

- 优化项目指令的计划文档语言规则：要求生成或维护 `docs/plans/` 计划时使用用户默认语言，并将现有计划文档正文统一翻译为简体中文。

### Fixed（修复）

- 加固 BAC 信任等级验证：拒绝伪造的 `signed` 与无有效 receipt 的 `anchored`，并让 `anchor.require` 配置在 `bac verify` 中默认生效。
- 加固 `.bac` 读取边界：对容器总大小、事件数量和单个 JSON 成员解压大小设置上限，降低恶意 ZIP/JSON 输入导致资源耗尽的风险。
- 加固锚定客户端和 reference server：`anchor push` 默认拒绝不安全 URL 和解析到内网的域名，支持通过参数或环境变量发送生产写入 token；生产模式要求 token 保护写入、管理页面和账本查询，并增加请求体限制、速率限制、旧 SQLite 迁移和 `ledger_id = null` 幂等处理。

## [1.2.0] - 2026-05-30

### Added（新增）

- 新增 private-anchor 客户端核心：支持盲化 `anchor_hash`、anchor request/receipt schema 校验、Ed25519 receipt 验签、anchored checkpoint 和 `verify --require-anchor`。
- 新增 CLI 锚定工作流：`bac init --mode/--anchor-url`、`bac config set/get`、`bac anchor request/import/push`，保持本地记录优先，远程失败不影响已有账本。
- 新增 `server/` reference anchor server：提供 FastAPI + SQLite + Ed25519 签名的 `/healthz`、public keys、anchor 创建、receipt 查询、ledger receipt 查询和只读 `/admin` 页面，并补充 Docker Compose 部署说明。
- 新增 `anchor` 与 `server` 可选依赖组，用于签名验签和自托管服务端。

### Changed（变更）

- 调整 private-anchor 实施计划：从单纯外部锚定协议升级为本地端与 `./server` 自托管服务端协同的完整路线，明确默认 `hybrid` 模式、可选 `local` 模式、Docker 部署、后台管理、服务端 API、密钥管理和隐私边界。
- 将 `anchor_status` 从单一 `anchored` 细分为 `not_anchored`、`local_checkpoint`、`receipt_valid` 和 `receipt_invalid`，更准确区分本地 checkpoint 与远程 signed receipt。

## [1.1.2] - 2026-05-30

### Added（新增）

- 新增 PyPI 发布流程：加入 GitHub Actions Trusted Publishing 工作流、发布指南和 README 入口，并补充 PyPI 元数据。

### Fixed（修复）

- 修正 README 与中文 README 抬头徽章：版本展示改为读取 GitHub 最新 tag，避免硬编码版本号与发布标签漂移；同时明确 `.bac` 格式徽章标签。

### Changed（变更）

- 根据 `init-project` 最新规范优化 `AGENTS.md`：补充 BAC 默认贡献记录约定、Single Source of Truth 维护规则，以及 `CLAUDE.md` 引用关系检查要求。
- 补充发布版本约束：执行发布任务时版本永远以用户明确指定值为准，并要求先同步 `pyproject.toml` 后再打 tag 与发布。

## [1.1.0] - 2026-05-29

### Added（新增）

- 新增中文 README 入口 `README.zh-CN.md`，与英文主 README 形成双语文档结构，便于中英文读者阅读项目定位、快速开始、格式说明和安全边界。
- 新增 MIT `LICENSE` 文件，使 README 许可证徽章与项目元数据指向一致。

### Changed（变更）

- 将 `.bac` 从 v1 JSON Lines 账本重构为 v2 单文件 ZIP 容器：内部包含 `manifest.json` 和连续编号的 `events/*.json` 事件条目，保持用户侧单文件体验，同时为后续 artifacts、checkpoint、签名和索引扩展预留空间。
- 将事件格式升级为 `bac.event.v2`，验证器新增 v2 容器检查，包括 ZIP 有效性、重复内部路径、事件编号缺口、manifest 与 genesis 一致性，以及原有哈希链和 checkpoint 验证。
- 将项目版本升级到 `2.0.0`。该版本不兼容未正式使用的 v1 JSON Lines `.bac` 文件。
- 更新 README 的产品定位说明，明确 BAC 是 AI-人类协作过程记录与辅助审计系统，不替代论文署名、作者贡献声明或最终学术责任判断。
- 将 README 调整为英文主文档，并参考 `bensz-channel` 的开源项目首页风格增加居中抬头、徽章、语言切换和 Star History 展示。

## [1.0.0] - 2026-05-28

### Added（新增）

- 明确项目定位为 `bensz auto contribution` 贡献归因与审计系统，核心产物为 `.bac` 文件。
- 补充 `.bac` 的初始设计边界：追加式事件、哈希链、签名、项目上下文绑定与篡改可发现。
- 新增 `.bac` 系统架构设计方案，说明轻核心、多适配器、事件模型、验证流程、威胁模型与 MVP 路线。
- 实现 BAC MVP：新增 Python CLI 与 library，支持 `bac init`、`bac record`、`bac verify`、`bac inspect`，以 JSON Lines 追加式事件账本记录贡献。
- 新增 canonical JSON、SHA-256 哈希链、项目上下文绑定、本地 checkpoint、敏感信息脱敏、贡献时间线展示和机器可读验证报告。
- 新增测试覆盖 canonicalization、哈希链验证、篡改检测、脱敏策略、checkpoint 和 CLI 端到端流程。
- 新增 BAC 工作原理教程，解释 `.bac` 事件字段、CLI 参数映射、哈希链验证机制和当前安全边界。

### Changed（变更）

- 将项目名、Python 发行包名、说明文档和工作区文件统一调整为 `bensz-auto-contribution`，使仓库命名与产品定位保持一致。
- 将初始化生成的通用项目说明替换为面向 AI tool 集成和人机贡献区分的项目指令。
- 更新 README，补充安装方式、CLI 使用示例、`.bac` 格式说明、MVP 安全边界和验证命令。

## [0.1.0] - 2026-05-26

### Added（新增）

- 初始化 AI 项目指令文件：生成 `AGENTS.md`、`CLAUDE.md`、`README.md` 与 `.gitignore`
- 配置项目工程原则、工作流和变更记录规范

### Changed（变更）

### Fixed（修复）

---

## 记录规则

- 必须记录影响项目行为、结构、工作流、工程原则、指令文件或关键配置的变更
- 记录应说明改了什么、为什么改，以及影响范围
- 版本号遵循 SemVer：bug fix 递增修订号，新功能递增次版本号，破坏性变更递增主版本号

```markdown
## [版本号] - YYYY-MM-DD

### Added（新增）
- 新增了 XXX：用途是 YYY

### Changed（变更）
- 修改了 XXX：原因是 YYY，影响是 ZZZ

### Fixed（修复）
- 修复了 XXX：表现是 YYY，修复方式是 ZZZ
```
