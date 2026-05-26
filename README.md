# auto-contribution

`auto-contribution` 是 `bensz auto contribution` 的原型项目，目标是为 AI 编程工具提供一个可调用的 tool，用来创建和验证 `.bac` 文件。

`.bac` 文件用于记录某个具体项目里人类与 AI 的贡献边界。它应该忠实记录需求来源、AI 生成内容、工具执行结果、人工确认、文件改动与验证证据，并通过哈希链、签名和追加式事件模型让篡改行为可发现。

## 特性

- 贡献归因：区分 human、ai、tool、system 等来源
- 追加式记录：避免覆盖历史事件，保留贡献时间线
- 篡改可发现：通过摘要链、签名和上下文绑定验证 `.bac` 完整性
- 项目绑定：将贡献记录关联到仓库状态、文件路径和操作证据
- AI tool 集成：面向 Codex、Claude Code 等 AI 编程环境设计调用接口

## 快速开始

### 环境要求

- 项目仍处于初始化阶段，运行环境和语言栈待实现阶段确定

### 安装

```bash
# 待实现
```

### 使用

```bash
# 待实现：创建、追加和验证 .bac 文件
```

## 设计边界

- `.bac` 的安全目标是 tamper-evident，即篡改可发现，不宣称文件本身绝对无法被修改
- `.bac` 不应记录敏感密钥、完整私有提示词或无关用户隐私
- 任何 AI 贡献记录都应尽量关联实际 diff、命令输出、测试结果或用户确认
- 涉及签名、哈希、身份和时间戳的逻辑必须有测试覆盖

## 目录结构

```
auto-contribution/
├── AGENTS.md
├── CHANGELOG.md
├── CLAUDE.md
├── docs
│   └── plans
├── README.md
├── Prompts.md
├── .gitignore
└── auto-contribution.code-workspace
```

## AI 辅助开发

本项目配置了 AI 辅助开发支持，可以使用以下工具进行智能开发：

### Claude Code

使用 `CLAUDE.md` 作为项目指令。

```bash
# 在项目目录启动 Claude Code
claude

# Claude Code 会自动读取 CLAUDE.md 理解项目上下文
```

### OpenAI Codex CLI

使用 `AGENTS.md` 作为项目指令。

```bash
# 在项目目录启动 Codex CLI
codex

# Codex 会自动读取 AGENTS.md 理解项目上下文
```

### AI 开发最佳实践

1. **新功能开发**：描述需求，AI 会按照项目工作流进行开发
2. **代码审查**：请求 AI 审查代码，它会按照工程原则给出建议
3. **文档更新**：AI 会自动同步更新相关文档
4. **问题排查**：描述问题现象，AI 会分析并给出解决方案
5. **变更记录**：**重要** - 凡是项目的更新，都要统一在 `CHANGELOG.md` 文件里记录。这是项目管理的强制性要求。

## 贡献

欢迎围绕 `.bac` 文件格式、威胁模型、AI tool 协议、验证机制和开发者体验提交 Issue 和 Pull Request。

## 许可证

MIT License
