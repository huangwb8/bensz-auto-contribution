# VS Code Marketplace 发布

## 发布目标与版本

插件源码和包配置位于 `softwares/vscode-plugin`。版本以该目录的 `package.json` 为准，与根目录 Python 包版本独立。用户指定发布版本时先同步插件版本；未指定时使用现有版本，不自动升级。

当前发布版本为 `0.1.0`，发布者 ID 为 `bensz`，插件 ID 为 `bensz.bac-viewer`。2026-10-03 用户已通过 Marketplace 网页上传 VSIX，管理页截图显示 `Public` 与 `Verifying 0.1.0`；随后公开页面返回 HTTP 404，公开查询尚未返回已验证版本。因此当前确认的是上传提交成功，上架和下载仍待平台验证。发布者必须由账号实际拥有或授予发布权限；仅设置 `publisher` 字段不能取得该身份。发布结果以 Marketplace 为准。

## 账号与凭据

- 使用 Microsoft 账号登录 [Marketplace 发布管理页](https://marketplace.visualstudio.com/manage)，创建发布者或确认现有发布权限。
- 微软当前要求新建 Azure DevOps 组织时关联有效 Azure 订阅；账号需对该订阅具有 Owner 或 Contributor 权限。已有组织及免费额度不受此新增要求影响。没有订阅时先按 Azure 页面完成订阅注册，再回到组织创建页选择订阅；组织托管区域不决定插件面向哪些国家发布。订阅费用与所选方案、实际使用量有关，开通时核对条款。参见 [创建组织](https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/create-organization?view=azure-devops)。
- 在同一账号下的 Azure DevOps 创建 Personal Access Token（PAT）。Organization 选择 **All accessible organizations**；Scopes 选择 **Custom defined → Show all scopes → Marketplace → Manage**，设置合适的有效期。
- PAT 仅用于发布身份验证，不写入 Git、文档、BAC 账本或 VSIX，不在命令参数或输出中显示。可通过 `vsce login` 输入，或仅向发布进程注入 `VSCE_PAT`。
- 本次协作可使用仓库外的本机文件 `~/.config/bac-marketplace/pat`，文件权限设为 `600`；发布完成后可由用户删除文件并撤销短期 PAT。

完整步骤以微软的 [官方发布指南](https://code.visualstudio.com/api/working-with-extensions/publishing-extension) 为准。

微软宣布全球 PAT 于 **2026-12-01** 退役。当前 PAT 路线适用于这次发布；后续自动发布需要迁移到 Microsoft Entra ID，官方推荐工作负载身份联合与托管身份。本文的 `vsce login` 示例不能视为该日期之后的长期认证方案。网页上传 VSIX 也可完成发布，无需为了网页上传创建 DevOps 组织或 PAT。

## 本地检查

在项目根目录执行：

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

以下示例使用当前版本和发布者；身份或版本变化时同步调整：

```bash
npx vsce login bensz
npx vsce verify-pat bensz
npx vsce publish --packagePath dist/bac-viewer-0.1.0.vsix
```

上传直接使用已检查的文件，不重新生成包，不自动创建 Git 提交或 tag。`verify-pat` 是身份访问检查，实际发布仍要求发布者具备写权限。不要用跳过重复版本选项把“已有版本”误报为本次发布成功。

若目标版本已经存在，先检查远端内容与本地包，不删除商店版本或自行递增版本。上传失败时核对凭据、发布者和权限，再按实际错误处理。

## 发布后验证

Marketplace 处理上传可能需要几分钟。只有在商店能查到正确 ID 和版本后，才把安装说明改为“已发布”。

若使用当前 ID，商店页面为：

<https://marketplace.visualstudio.com/items?itemName=bensz.bac-viewer>

用户安装命令为：

```bash
code --install-extension bensz.bac-viewer
```

发布证据应记录插件 ID、版本、VSIX SHA-256、上传结果和远端查询结果。BAC 中分别记录 human 的发布请求、ai 的准备工作、tool 的检查／上传结果；未经观察的结果不能写为成功，平台状态只在取得实际响应后记录。

## 支持范围

当前插件支持桌面 VS Code 1.90+ 与远程 Node 扩展宿主，不支持浏览器版 `vscode.dev`。界面当前为简体中文。查看与 Git 比较不依赖 Python；完整验证需在扩展宿主所在环境安装 BAC CLI。插件只读账本，BAC 是 tamper-evident 的辅助过程记录，不能单独证明身份或最终署名。
