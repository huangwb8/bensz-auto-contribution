# BAC 安全加固实施计划

> **给 Claude：** 必须使用子技能 `superpowers:executing-plans`，按任务逐步执行本计划。

**目标：** 修复 2026-05-31 已确认的 BAC 信任语义与锚定服务端安全漏洞。

**架构：** 将 `.bac` 视为不可信输入，并把事件信任等级建模为严格状态机。锚定服务端的生产环境加固保持小而明确：认证、有界输入、速率限制，以及带访问控制的元数据端点。

**技术栈：** Python 3.10+，标准库 ZIP/JSON/urllib，FastAPI，SQLite，pytest，可选 `cryptography`。

**最小变更范围：** 按需修改 `src/bac/**`、`server/app/**`、`server/tests/**`、`tests/**`、`server/docker-compose.yml`、`server/README.md`、`docs/bac-tutorial.md` 和变更记录文档。避免无关格式化、功能扩张，除非测试确有要求，否则不修改 BAC 格式名称。

**成功标准：** 伪造的 `signed` 或 `anchored` 信任等级无法通过验证；默认执行 `anchor.require` 配置；生产环境锚定 API 拒绝未认证的写入、管理后台和账本读取请求；请求体和 ZIP 限制能阻断资源耗尽；`anchor push` 默认拒绝不安全 URL；所有测试通过。

**验证计划：** 运行 `python -m pytest -q`；运行下列定向安全测试；在 CI 或固定超时内运行 `python -m pip_audit . --progress-spinner off --skip-editable`。

---

## 所需代理

- @security-specialist：审查威胁模型和漏洞利用防护。
- @tdd-workflow：编写先失败的安全回归测试。
- @code-reviewer：合并前进行代码审查。
- @documentation-specialist：更新定义安全边界的文档。

## 假设

- `signed` 尚未实现，因此普通事件不能自称拥有 signed 信任等级。
- `anchored` 只对携带有效远程锚定 receipt 的 checkpoint 事件有效。
- 本地开发可以保留未认证的锚定 smoke test，但生产模式必须默认失败关闭。

## 非目标

- 不添加完整用户系统或 RBAC。
- 不重新设计 BAC v2 容器格式。
- 不引入可选部署层控制之外的外部服务。

## 任务 1：强制执行信任等级语义

**文件：**

- 修改：`src/bac/core/verify.py`
- 修改：`src/bac/service/event_builder.py`
- 修改：`src/bac/adapters/cli.py`
- 测试：`tests/test_bac_core.py`
- 测试：`tests/test_anchor_core.py`

**步骤 1：编写失败测试**

添加测试，创建包含以下内容的事件：

- `trust_level: signed` 且 `signature: null`
- 非 checkpoint 事件上的 `trust_level: anchored`
- 没有有效 receipt 的 `trust_level: anchored` checkpoint

修复前预期：测试失败，因为当前验证器会无错误接受这些事件。

**步骤 2：实现最小校验**

- 在 CLI 中移除或限制通用 `record` 的 `--trust-level signed` 与 `--trust-level anchored`。
- 在 builder 中拒绝 `signed`，除非签名实现提供了签名。
- 在 verifier 中，如果 `trust_level == "signed"` 且签名验证无效，则失败。
- 在 verifier 中，如果 `trust_level == "anchored"` 且事件不是有效锚定 checkpoint，则失败。

**步骤 3：验证**

运行：

```bash
python -m pytest tests/test_bac_core.py tests/test_anchor_core.py -q
```

预期：伪造信任等级测试在实现前失败，在实现后通过。

## 任务 2：执行 `anchor.require` 配置

**文件：**

- 修改：`src/bac/adapters/cli.py`
- 测试：`tests/test_anchor_core.py`
- 文档：`docs/bac-tutorial.md`

**步骤 1：编写失败测试**

创建账本，设置 `anchor.require true`，然后在不传 `--require-anchor` 的情况下运行 `bac verify --json`。

修复前预期：状态为 warn/pass。修复后预期：没有有效 receipt 时状态为 fail。

**步骤 2：实现**

在 `_cmd_verify` 中从事件读取 BAC 配置，并计算：

```python
effective_require_anchor = args.require_anchor or bool(config.get("anchor.require"))
```

将该值传给 `verify_bac_file`。

**步骤 3：验证**

运行：

```bash
python -m pytest tests/test_anchor_core.py -q
```

## 任务 3：加固锚定 API 的生产访问控制

**文件：**

- 修改：`server/app/core/config.py`
- 修改：`server/app/main.py`
- 测试：`server/tests/test_anchor_api.py`
- 文档：`server/README.md`

**步骤 1：编写失败测试**

生产模式应满足：

- 没有 token 时拒绝 `POST /api/v1/anchors`
- 没有 admin token 时拒绝 `/admin`，或在禁用时返回 404
- 没有 read token 时拒绝 `/api/v1/ledgers/{ledger_id}/receipts`

**步骤 2：实现**

- 添加 `BAC_ANCHOR_API_TOKEN`、`BAC_ANCHOR_ADMIN_TOKEN` 和 `BAC_ANCHOR_ENABLE_LEDGER_QUERY` 配置。
- 生产模式下，对写入端点和元数据端点要求 token。
- 保持 `/healthz` 和 `/api/v1/public-keys` 公开。

**步骤 3：验证**

运行：

```bash
python -m pytest server/tests/test_anchor_api.py -q
```

## 任务 4：添加请求体限制和速率限制

**文件：**

- 修改：`server/app/main.py`
- 测试：`server/tests/test_anchor_api.py`

**步骤 1：编写失败测试**

- 缺失 `Content-Length` 且请求体超限时返回 413。
- 生产模式下，同一客户端请求过多时返回 429。

**步骤 2：实现**

- 添加中间件，按实际读取字节数计数，而不是只相信 header 值。
- 添加适合 reference server 的简单进程内 token bucket，并在文档中说明生产环境可使用反向代理限制。

**步骤 3：验证**

运行：

```bash
python -m pytest server/tests/test_anchor_api.py -q
```

## 任务 5：限制 `.bac` ZIP 与 JSON 读取

**文件：**

- 修改：`src/bac/core/verify.py`
- 修改：`src/bac/storage/bac_file.py`
- 测试：`tests/test_bac_core.py`

**步骤 1：编写失败测试**

创建以下 fixture：

- 事件成员数量过多
- 单个事件成员解压后过大
- BAC 文件总字节数过大

**步骤 2：实现**

定义保守常量，例如：

```python
MAX_BAC_BYTES = 50 * 1024 * 1024
MAX_EVENT_COUNT = 100_000
MAX_MEMBER_UNCOMPRESSED_BYTES = 2 * 1024 * 1024
```

读取成员字节前，检查 `Path.stat().st_size`、`ZipInfo.file_size` 和事件数量。

**步骤 3：验证**

运行：

```bash
python -m pytest tests/test_bac_core.py -q
```

## 任务 6：默认阻止不安全锚定 URL

**文件：**

- 修改：`src/bac/adapters/cli.py`
- 测试：`tests/test_anchor_core.py`
- 文档：`docs/bac-tutorial.md`

**步骤 1：编写失败测试**

将 `anchor.url` 配置为 `http://127.0.0.1:8080`、`http://169.254.169.254` 和私有 RFC1918 地址。

修复后预期：除非存在明确开发覆盖开关，否则 `bac anchor push` 拒绝这些地址。

**步骤 2：实现**

- 使用 `urllib.parse.urlparse` 解析。
- 默认只允许 `https://`。
- 拒绝 loopback、link-local、private、multicast 和非 HTTP(S) scheme。
- 为本地开发添加 `--allow-insecure-anchor-url`。

**步骤 3：验证**

运行：

```bash
python -m pytest tests/test_anchor_core.py -q
```

## 任务 7：修复并发下的锚定幂等性

**文件：**

- 修改：`server/app/db/session.py`
- 修改：`server/app/main.py`
- 测试：`server/tests/test_anchor_api.py`

**步骤 1：编写失败测试**

发送并发的相同请求，其中 `ledger_id: null`，并验证只产生一条数据库记录和一个 receipt identity。

**步骤 2：实现**

- 在存储中把缺失的 `ledger_id` 规范化为空字符串，或使用 `COALESCE` 创建表达式唯一索引。
- 使用事务和 `INSERT ... ON CONFLICT`，或等价的写后读取逻辑。

**步骤 3：验证**

运行：

```bash
python -m pytest server/tests/test_anchor_api.py -q
```

## 任务 8：文档、变更记录和安全审查

**文件：**

- 修改：`README.md`
- 修改：`README.zh-CN.md`
- 修改：`docs/bac-tutorial.md`
- 修改：`server/README.md`
- 修改：`CHANGELOG.md`

**步骤 1：更新文档**

记录：

- `signed` 在事件签名实现前不可用。
- `anchored` 仅表示 checkpoint 上存在有效远程 receipt。
- 生产环境锚定部署要求 token、持久化密钥、速率限制和请求体限制。
- 不安全 `anchor.url` 行为和本地覆盖开关。

**步骤 2：运行完整验证**

运行：

```bash
python -m pytest -q
python -m pip_audit . --progress-spinner off --skip-editable
```

**步骤 3：审查**

运行 @code-reviewer，并重点关注：

- 信任状态绕过
- SSRF 边界情况
- 请求体限制绕过
- 并发与幂等性
- 向后兼容性和文档准确性

## 回滚说明

- 信任语义变更可能拒绝此前创建的无效账本。应将其记录为验证器加固变更。
- 锚定 token 执行应仅限生产模式，以保留本地 smoke test。
- ZIP 限制之后只有在真实用户确有合法超大账本需求时，才考虑配置化。
