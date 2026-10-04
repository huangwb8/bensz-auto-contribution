# VS Code Marketplace 发布

## 发布目标与版本

插件源码和包配置位于 `softwares/vscode-plugin`。版本以该目录的 `package.json` 为准，与根目录 Python 包版本独立。用户指定发布版本时先同步插件版本；未指定时使用现有版本，不自动升级。

本次目标版本为 `0.1.2`，发布者 ID 为 `bensz`，插件 ID 为 `bensz.bac-viewer`。版本与发布状态分别核对；本地打包不代表已经上传或公开上架。

## 默认发布流程

按根目录 `AGENTS.md`：AI 完成本地检查与打包 → 人类在官方 Marketplace 网页手动上传 → 确认提交成功后在本机安装同一 VSIX → 核对商店公开版本。

提交上传、本机安装与商店公开可用是不同状态，应分别报告。平台仍在验证时，确认上传提交成功即可先在本机安装该 VSIX；不能因此声称新版本已公开上架。

## 账号与凭据

人类使用拥有 `bensz` 发布权限的 Microsoft 账号登录 [Marketplace 发布管理页](https://marketplace.visualstudio.com/manage)，完成验证码、MFA 和上传。AI 提供 VSIX 路径、版本与 SHA-256，不接管浏览器登录会话。密码、Cookie、登录状态和其它凭据不写入 Git、文档、BAC、VSIX 或发布证据。

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

1. 人类打开官方管理页，确认当前账号拥有目标发布者权限。
2. 选择 `bensz` 发布者与 `bac-viewer` 插件，在更多操作中选择 **Update**；首次发布使用创建扩展入口。
3. 选择 AI 提供的 `dist/bac-viewer-<插件版本>.vsix`，核对插件 ID 与目标版本，再点击 **Upload**。
4. 检查网页成功／失败响应及管理页实际版本与处理状态，将结果告知 AI；仅点击按钮或页面跳转不能作为上传成功证据。
5. 若目标版本已存在，先核对远端内容与本地包，不删除已有版本或自行递增版本；上传失败先处理错误。

## 本机安装最新版本

用户确认上传提交成功或提供相应证据后，在插件目录直接安装本次上传的同一 VSIX：

```bash
VERSION=$(node -p "require('./package.json').version")
code --install-extension "dist/bac-viewer-${VERSION}.vsix" --force
code --list-extensions --show-versions
```

检查安装命令成功退出，并确认列表中的 `bensz.bac-viewer@<插件版本>` 与上传版本一致。若 `code` 不在 PATH，使用本机 VS Code 的 CLI 可执行文件路径。必要时执行 **Developer: Reload Window** 加载新版本。

使用本次上传的本地包，可以在 Marketplace 尚未完成验证时安装最新版本；从商店按插件 ID 安装可能仍获得旧版本。

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
