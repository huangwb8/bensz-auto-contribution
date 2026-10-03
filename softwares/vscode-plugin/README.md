# BAC 贡献账本 · VS Code 插件

点击 `.bac` 文件，阅读人类与 AI 的协作过程；结合 Git 查看贡献账本和源代码的变化。查看器只读，不修改账本，不执行事件里记录的命令。

## 使用

- 从 VS Code 资源管理器打开 `.bac`，默认显示贡献时间线；按人类、AI、工具、系统筛选，也可搜索摘要、事件类型和关联文件。
- 点击记录查看来源、时间、信任声明、Git 提交、文件 hash、命令输出证据与原始 JSON。
- 切换到 **Git 变化**，选择 **HEAD → 工作区**、**HEAD → 暂存区** 或 **暂存区 → 工作区**，点击 **比较账本**。
- 比较展示事件新增、修改、删除、重排和 manifest／项目绑定变化。点击变化后可打开 VS Code 原生事件 JSON diff。
- 关联文件支持打开文件，以及 **HEAD → 工作区**、**HEAD → 暂存区**、**记录时提交 → 工作区** 的原生代码 diff。
- 点击 **验证账本**，显示原有 BAC 验证器的完整报告。文件更新后自动刷新并清除旧验证结果；Git 版本变化可手动重新比较。

资源管理器右键与源代码管理中的 `.bac` 条目提供 **BAC: 与 HEAD 比较账本**；命令面板提供打开、比较、验证命令。

## 安装

需要桌面版 VS Code 1.90+；查看与 Git 比较不依赖 Python。在项目根目录构建并安装：

```bash
cd softwares/vscode-plugin
npm ci
npm run package
code --install-extension dist/bac-viewer-0.1.0.vsix --force
```

也可在 VS Code 扩展面板的菜单中选择 **从 VSIX 安装**。已打开的 `.bac` 可右键标签，选择 **重新打开编辑器的方式 → BAC 贡献账本**；如安装后没有自动切换，可执行 **Developer: Reload Window**。

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
- 验证在权限受限的临时快照上执行，快照使用后删除；检查结果与当前文件摘要、事件数量及 head 绑定，文件变化使结果失效。验证报告只对应工作区账本，Git 比较结果不自动获得验证状态。
- 事件签名与锚定状态按 BAC 验证器报告显示。当前核心验证器尚不支持一般事件签名；远程锚定 receipt 的验证能力由 BAC 环境决定。
- 未受信任工作区仅允许读取账本。可信工作区中的程序调用使用参数数组，不通过 shell；Webview 禁止外部资源，账本内容均以纯文本显示。
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

`npm test` 进行 TypeScript 检查和容器／比较／路径测试。`test:vscode` 在独立扩展宿主中检查界面交互和原生代码 diff，并在项目 `tmp/img-frontend/run-YYYYMMDD-HHMMSS` 保存前后 JPG 截图；默认使用 macOS `/Applications/Visual Studio Code.app/Contents/MacOS/Code`，其它安装位置可用 `BAC_VSCODE_EXECUTABLE` 指定。测试需要本机 VS Code、Git 和可执行的 BAC CLI。

## 许可

MIT。扩展构建内含 `yauzl` 及其依赖，第三方许可见 `THIRD_PARTY_NOTICES.md`。
