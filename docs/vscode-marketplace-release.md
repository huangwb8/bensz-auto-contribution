# VS Code Marketplace 发布

## 发布目标与版本

插件源码和包配置位于 `softwares/vscode-plugin`。版本以该目录的 `package.json` 为准，与根目录 Python 包版本独立。用户指定发布版本时先同步插件版本；未指定时使用现有版本，不自动升级。

本次目标版本为 `0.1.4`，发布者 ID 为 `bensz`，插件 ID 为 `bensz.bac-viewer`。版本与发布状态分别核对；本地打包不代表已经上传或公开上架。

## 默认发布流程

按根目录 `AGENTS.md`：AI 完成本地检查与打包 → 人类在官方 Marketplace 网页手动上传 → 确认提交成功后在本机安装同一 VSIX → 核对商店公开版本。

提交上传、本机安装与商店公开可用是不同状态，应分别报告。平台仍在验证时，确认上传提交成功即可先在本机安装该 VSIX；不能因此声称新版本已公开上架。

## 账号与凭据

人类使用拥有 `bensz` 发布权限的 Microsoft 账号登录 [Marketplace 发布管理页](https://marketplace.visualstudio.com/manage)，完成验证码、MFA 和上传。AI 提供 VSIX 路径、版本与 SHA-256，不接管浏览器登录会话。密码、Cookie、登录状态和其它凭据不写入 Git、文档、BAC、VSIX 或发布证据。

## 打包工具依赖

`package.json` 对 `@vscode/vsce` 的 `minimatch` 定向使用 `11.0.1+`，用于修复上游依赖范围尚未覆盖的 glob 模式放大拒绝服务告警（[GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249)）。该依赖只用于本地打包，不进入插件运行包；修改覆盖规则后需重跑 `npm ci`、`npm test`、`npm run package` 和 VSIX 内容检查。

## 本地检查

使用 Node.js 22.12+（或受支持的更新版本），在项目根目录执行：

```bash
cd softwares/vscode-plugin
npm ci
npm test
npm run package
```

打包产物为 `dist/bac-viewer-<插件版本>.vsix`。`vscode:prepublish` 自动执行 TypeScript 检查和构建；运行依赖已通过 esbuild 打入 `dist/extension.js`，因此打包使用 `--no-dependencies`。

检查 VSIX 内的 manifest、版本、发布者、主程序、Webview 资源和 MIT／第三方许可。不能包含 `node_modules`、测试、开发源码、环境文件、凭据或本机元数据。

当前界面验证记录位于 `docs/plans/2026-10-03-vscode-bac-viewer.md`。需要重验实际桌面交互时执行 `npm run test:vscode`；该测试默认使用 macOS VS Code 路径，其它路径通过 `BAC_VSCODE_EXECUTABLE` 指定。不要把 macOS 验证结果描述为 Windows／Linux 验证。

## 上传已检查的 VSIX

以下步骤用于更新已有的 `bensz.bac-viewer`，通过网页上传 VSIX 即可，无需为此配置 PAT 或 Azure DevOps。首次发布新插件时才使用创建扩展入口。

### 打开发布管理页

1. 打开 [Marketplace 发布管理页](https://marketplace.visualstudio.com/manage)。
2. 使用之前发布插件、拥有目标发布者权限的 **Microsoft 账号**登录，按提示完成验证码或 MFA。
3. 在发布者列表中选择 **`bensz`**。如果看不到该发布者，先检查是否登录了正确账号及该账号是否拥有发布权限。

### 选择已有插件

4. 找到 **BAC Contribution Ledger**，核对插件 ID 为 **`bensz.bac-viewer`**。
5. 打开该插件的操作菜单，选择 **Update**（更新）。沿用已有插件和发布者，不要创建新插件或修改身份。

### 选择并核对 VSIX

6. 选择本次经过检查的 `softwares/vscode-plugin/dist/bac-viewer-0.1.4.vsix`。后续发布时，以 `package.json` 中的目标版本及 AI 提供的已检查产物为准，不要误选旧文件。

   在 macOS 文件选择窗口中可按 **⌘⇧G**，粘贴当前项目下的 `softwares/vscode-plugin/dist/` 完整路径，再选择文件。本机当前路径为：

   ```text
   /Volumes/2T01/Github/bensz-auto-contribution/softwares/vscode-plugin/dist/
   ```

   换设备或移动仓库后，应使用新的项目路径。在仓库根目录可以执行以下命令核对 AI 提供的 SHA-256：

   ```bash
   shasum -a 256 softwares/vscode-plugin/dist/bac-viewer-0.1.4.vsix
   ```

7. 上传前核对目标和所选文件：

   | 项目 | 本次正确值 |
   |---|---|
   | 发布者 | `bensz` |
   | 插件 | `bac-viewer` |
   | 插件 ID | `bensz.bac-viewer` |
   | 新版本 | `0.1.4` |
   | 文件 | `bac-viewer-0.1.4.vsix` |

   版本号由 VSIX 内的配置提供，通常无需在网页手动填写；后续版本按实际目标替换。

### 上传并确认结果

8. 点击 **Upload**，等待页面返回结果。若提示目标版本已存在，先核对管理页版本与远端内容，不删除已有版本或自行递增版本；其它上传错误也应先处理后重试。
9. 确认页面明确显示上传提交成功，且管理页出现目标版本。保留成功提示或截图，将结果告知 AI；仅点击按钮或页面跳转不能作为成功证据。收到确认后，AI 安装本次上传的同一 VSIX 并核对本机版本。
10. 若管理页显示验证或处理中，等待平台完成。打开 [插件商店页面](https://marketplace.visualstudio.com/items?itemName=bensz.bac-viewer)，确认公开版本已变为目标版本后，再认定已经上架。本次目标为 **`0.1.4`**；上传提交成功与商店公开可用分别记录。

## 本机安装最新版本

用户确认上传提交成功或提供相应证据后，在插件目录直接安装本次上传的同一 VSIX：

```bash
VERSION=$(node -p "require('./package.json').version")
code --install-extension "dist/bac-viewer-${VERSION}.vsix" --force
code --list-extensions --show-versions
```

检查安装命令成功退出，并确认列表中的 `bensz.bac-viewer@<插件版本>` 与上传版本一致。若 `code` 不在 PATH，使用本机 VS Code 的 CLI 可执行文件路径。必要时执行 **Developer: Reload Window** 加载新版本。

使用本次上传的本地包，可以在 Marketplace 尚未完成验证时安装最新版本；从商店按插件 ID 安装可能仍获得旧版本。

### 核对窗口实际加载的版本

CLI 列出的安装版本与现有窗口加载的版本需要分别核对。源代码管理直接打开 `.bac` 需要插件 `0.1.2` 或更高版本；`0.1.0`／`0.1.1` 在 `openCustomDocument` 中拒绝 `git:` 资源，会显示“由于意外错误，无法打开编辑器”。

在出错窗口执行 **Developer: Reload Window（开发人员：重新加载窗口）**，关闭失败的比较标签，再点击源代码管理条目。若重试仍失败，点击 **显示日志**，查看 `bac-viewer-<版本>/dist/extension.js` 堆栈路径并检查该窗口的配置文件与启用版本。安装列表中的新版不能作为窗口已加载新版的证据。

## 发布后验证

Marketplace 处理上传可能需要几分钟。只有在商店能查到正确 ID 和版本后，才把安装说明改为“已发布”。

若使用当前 ID，商店页面为：

<https://marketplace.visualstudio.com/items?itemName=bensz.bac-viewer>

用户安装命令为：

```bash
code --install-extension bensz.bac-viewer
```

发布证据应记录插件 ID、版本、VSIX SHA-256、上传结果、本机安装命令与实际安装版本，以及远端查询结果。BAC 中分别记录 human 的发布规则或请求、ai 的准备工作、tool 的检查／安装／公开查询结果；人类上传反馈保留为 human 来源；未经观察的结果不能写为成功，平台状态只在取得实际响应后记录。

## 支持范围

当前插件支持桌面 VS Code 1.90+ 与远程 Node 扩展宿主，不支持浏览器版 `vscode.dev`。界面默认英文，用户可通过面板语言菜单或 `bacViewer.language=zh-CN` 主动选择简体中文；扩展名称和命令入口保持英文，README 提供英文主文档及中文版链接。查看与 Git 比较不依赖 Python；完整验证需在扩展宿主所在环境安装 BAC CLI。插件只读账本，BAC 是 tamper-evident 的辅助过程记录，不能单独证明身份或最终署名。
