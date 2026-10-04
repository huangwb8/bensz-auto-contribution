# BAC 贡献账本 · VS Code 插件

[English](README.md) | 简体中文

点击 `.bac` 文件，阅读人类与 AI 的协作过程；结合 Git 查看贡献账本和源代码的变化。查看器只读，不修改账本，不执行事件里记录的命令。

## 界面语言

界面默认使用英文，不跟随 VS Code 的显示语言自动切换。用户可在账本面板右上角选择 **简体中文**，也可在 VS Code 设置中将 `bacViewer.language` 改为 `zh-CN`；选择 **English** 或设置为 `en` 可切回英文。选择保存在用户设置中，重新打开账本时继续生效。修改设置会重新加载已打开的查看器；搜索、来源筛选与所选事件会保留，Git 比较可重新执行。

扩展名称、命令面板入口和设置说明使用英文，便于按统一名称查找。账本内的摘要、命令、证据、原始 JSON 和外部验证器输出保留原文，不做翻译。

## 使用

- 从 VS Code 资源管理器打开 `.bac`，默认显示贡献时间线；按人类、AI、工具、系统筛选，也可搜索摘要、事件类型和关联文件。
- 从**源代码管理**直接点击 `.bac` 变更，查看 Git 版本；每侧标明实际来源，并分别保留时间线、筛选与验证状态。
- 点击记录查看来源、时间、信任声明、Git 提交、文件 hash、命令输出证据与原始 JSON。
- 切换到 **Git 变化**，选择 **HEAD → 工作区**、**HEAD → 暂存区** 或 **暂存区 → 工作区**，点击 **比较账本**。
- 比较展示事件新增、修改、删除、重排和 manifest／项目绑定变化。点击变化后可打开 VS Code 原生事件 JSON diff。
- 关联文件支持打开文件，以及 **HEAD → 工作区**、**HEAD → 暂存区**、**记录时提交 → 工作区** 的原生代码 diff。
- 点击 **验证账本**，显示原有 BAC 验证器的完整报告。工作区或暂存区内容更新后，对应视图刷新并清除旧验证结果；无关的 Git 状态更新保留有效报告，固定提交视图保持原版本。内容更新后可重新执行事件比较。

资源管理器右键与源代码管理中的 `.bac` 条目提供 **BAC: Compare Ledger with HEAD**；命令面板提供打开、比较、验证命令。

## 源代码管理的版本含义

已跟踪文件的**更改**比较暂存区基线与工作区，**暂存的更改**比较 HEAD 与暂存区。新增或删除时，VS Code 可能只打开已有的一侧。明确打开的版本没有账本时显示**版本不存在**；损坏容器、合并冲突、不可用提交和访问错误分别显示原因及恢复提示。

仅接收本地 `file:` 和内置 `git:` 资源。Git 地址必须绑定已打开的项目与实际仓库；空 ref 对应暂存区，`~` 遵循内置 Git 的“暂存区或 HEAD 基线”含义，`~1`–`~3`／`:1`–`:3` 对应具体合并阶段。打开 HEAD 时固定其版本，完整对象 ID 保留指定版本。拒绝任意 ref、子模块 diff 资源、畸形地址和不一致路径。

Git 账本通过有大小限制的 `git cat-file` 读取原始 blob，绕过 textconv 和内容过滤器，保证 ZIP 与验证快照保留仓库存储的字节。需要启用内置 Git 扩展并信任工作区；历史读取失败时不替换为当前工作区账本，也不启用提议版差异 API。

历史侧的**打开此版本的文件**读取该 Git 来源的关联代码。既有三种代码 diff 与 **BAC: Compare Ledger with HEAD** 继续按明确标注的 HEAD／暂存区／工作区范围执行。事件比较不改变时间线的版本，也不会将某侧验证状态借给另一侧。

## 安装

源码构建需要 Node.js 22.12+（或受支持的更新版本）。需要桌面版 VS Code 1.90+；查看与 Git 比较不依赖 Python。在项目根目录构建并安装：

```bash
cd softwares/vscode-plugin
npm ci
npm run package
code --install-extension dist/bac-viewer-0.1.2.vsix --force
```

也可在 VS Code 扩展面板的菜单中选择 **从 VSIX 安装**。已打开的 `.bac` 可右键标签，选择 **重新打开编辑器的方式 → BAC Contribution Ledger**；如安装后没有自动切换，可执行 **Developer: Reload Window**。

### 源代码管理仍提示编辑器错误

源代码管理的 Git 资源需要 **0.1.2 或更高版本**；0.1.0 和 0.1.1 会在查看器打开前拒绝这类资源。安装新版后，已打开的窗口可能仍加载旧代码。

先执行 `code --list-extensions --show-versions`，确认有 `bensz.bac-viewer@0.1.2`（或更高版本）。需要时安装上方已检查的 VSIX，再在出错窗口执行 **Developer: Reload Window（开发人员：重新加载窗口）**，关闭失败标签，重新点击源代码管理条目。若仍失败，点击 **显示日志**，检查堆栈中的 `bac-viewer-<版本>/dist/extension.js` 路径；若仍指向 `bac-viewer-0.1.0` 或 `bac-viewer-0.1.1`，说明该窗口仍在使用旧代码，还需检查当前 VS Code 配置文件及其中启用的插件版本。

### Marketplace 发布

公开发布遵循微软的 [扩展发布指南](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)，本项目操作说明位于仓库根目录 `docs/vscode-marketplace-release.md`。VSIX 文件名随 `package.json` 中的插件版本生成；插件版本与 Python 包版本独立。

### 完整验证

安装 BAC CLI：

```bash
python -m pip install bensz-auto-contribution
```

插件从 `PATH` 或 `~/.local/bin/bac` 查找工具。如果使用独立 Python 环境，在 VS Code 设置里将 `bacViewer.bacExecutable` 指向该环境的 `bac` 可执行文件。设置值是文件路径，不是 shell 命令。

远程 SSH／容器工作区中，插件、Git 与验证器运行在远程扩展宿主；BAC CLI 也应安装在远端。

## 格式、验证与安全边界

- 支持 `bac.container.v2` ZIP 和 `bac.event.v2` 事件；容器可读取不等于验证通过，打开时默认显示 **未验证**。
- 读取限制为压缩容器 50 MiB、单成员解压 2 MiB、累计解压 256 MiB、事件最多 100,000 条；拒绝重复条目、非连续事件编号、重复事件 ID、未知成员和无效 UTF-8／JSON。超过界面读取限制的账本仍可用 BAC CLI 检查。
- 账本比较检查事件 ID、实际解析内容及相对顺序，不仅比较事件自报的 hash。JSON 排版差异不计作贡献变化，完整验证另行检查内容摘要与哈希链。
- 验证在权限受限的临时快照上执行，快照使用后删除；每份结果与查看侧的资源 URI、原始快照摘要、事件数量及 head 绑定。验证期间来源变化会拒绝报告，内容更新后清除旧结果；工作区、暂存区与历史侧分别验证，事件比较不自动获得验证状态。
- 事件签名与锚定状态按 BAC 验证器报告显示。当前核心验证器尚不支持一般事件签名；远程锚定 receipt 的验证能力由 BAC 环境决定。
- 未受信任工作区仅允许读取本地账本，Git 资源仍被禁用。可信工作区中的程序调用使用参数数组，不通过 shell；Webview 禁止外部资源，账本内容均以纯文本显示。
- 路径跳转以当前 Git 仓库／工作区为根，不使用账本声明的绝对根目录；拒绝越界和符号链接逃逸。
- 代码 diff 支持最多 5 MiB 的 UTF-8 文本。缺少某一版本的文件按空文件呈现；合并冲突、二进制文件、非 UTF-8 和已丢失的 Git 提交会给出提示。
- 文件 hash 与 `diff --stat` 不能重建历史未提交内容，也不能证明每行代码的来源。BAC 是 tamper-evident 的辅助过程记录，不能单独证明身份、贡献完整性或最终署名。

## 开发与测试

插件实现、界面、测试、构建脚本与包配置只能存放于 `softwares/vscode-plugin` 及子目录；项目规则位于根目录 `AGENTS.md`。

```bash
npm test
npm run test:vscode
npm run package
```

`npm test` 进行 TypeScript 检查和容器／比较／路径及语言测试。`test:vscode` 在独立扩展宿主中检查语言切换与保留、界面交互和原生代码 diff，还在临时 Git 仓库中真实点击源代码管理，检查逐侧验证与生命周期，并运行真实不可信工作区测试，并在项目 `tmp/img-frontend/run-YYYYMMDD-HHMMSS` 保存前后 JPG 截图；默认使用 macOS `/Applications/Visual Studio Code.app/Contents/MacOS/Code`，其它安装位置可用 `BAC_VSCODE_EXECUTABLE` 指定。测试需要本机 VS Code、Git 和可执行的 BAC CLI。

执行 `BAC_VSCODE_VERSION=1.90.0 node scripts/test-scm.mjs` 可复测最低版本（下载官方独立副本）；`node scripts/test-scm.mjs --untrusted` 单独运行不可信工作区测试，其启动器避开 test-electron 自动添加的信任绕过参数。SCM 测试保存中英文版本标签、窄窗口及明暗主题 JPG 截图。

## 许可

MIT。扩展构建内含 `yauzl` 及其依赖，第三方许可见 `THIRD_PARTY_NOTICES.md`。
