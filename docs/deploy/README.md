# BAC Anchor Docker 部署

`docs/deploy` 是面向服务器管理员的 Compose 部署示例目录，只保留部署所需的配置文件与说明。仓库内的便捷运维脚本统一放在 `tools/`，避免把脚本混入可复制到服务器的文档部署包。

这套部署默认接入 `npm_default` 外部网络，容器命名保持 `{应用名}-{组件}` 风格：

- `bac-anchor-app`：BAC Anchor Server
- `bac-anchor-postgres`：PostgreSQL 持久化存储
- `bac-anchor-redis`：Redis 生产限流状态

## 准备配置

```bash
cd docs/deploy
cp .env.example .env
```

创建仅服务器可读的签名密钥文件，并生成 token/数据库密码后填入 `.env`：

```bash
install -d -m 0700 secrets
python - <<'PY'
import base64
import secrets
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

private_key = Ed25519PrivateKey.generate()
private_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
Path("secrets/anchor-private-key.b64").write_text(
    base64.b64encode(private_bytes).decode("ascii") + "\n",
    encoding="utf-8",
)
print("BAC_ANCHOR_API_TOKEN=" + secrets.token_urlsafe(48))
print("BAC_ANCHOR_ADMIN_TOKEN=" + secrets.token_urlsafe(48))
print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(32))
PY
chmod 0600 .env secrets/anchor-private-key.b64
```

`.env` 只参与 Compose 变量插值，不会整文件注入容器。Compose 将服务环境固定为 `production`，避免环境名拼写错误导致保护措施静默降级。签名私钥通过只读 Compose secret 仅挂载到应用容器；PostgreSQL 只接收自身三个初始化变量，Redis 不接收业务 secret。

`BAC_ANCHOR_RELEASE_VERSION` 与 `BAC_ANCHOR_IMAGE_DIGEST` 应固定到同一个已经核验的 DockerHub 发布。当前示例固定为 `1.3.2`。
这套部署使用 PostgreSQL 和 Redis，需要镜像版本包含服务端 PostgreSQL/Redis 支持；从源码部署时可取消 `docker-compose.yml` 中 `build` 配置的注释后本地构建。

## 启动或更新

```bash
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
```

应用端口只绑定服务器回环地址 `127.0.0.1:18080`，用于 SSH 隧道验收，不会直接暴露公网。PostgreSQL 与 Redis 只加入 `internal` 后端网络；只有应用同时加入 `npm_default` 代理网络。

查看日志：

```bash
docker compose logs -f --tail="${TAIL:-200}" bac-anchor-app
```

健康检查：

```bash
docker compose exec bac-anchor-app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5).read().decode())"
```

## 备份与恢复

备份 PostgreSQL：

```bash
mkdir -p backups
docker compose exec -T bac-anchor-postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "backups/bac-anchor-$(date +%Y%m%d-%H%M%S).sql"
```

恢复前建议先停止应用容器，避免写入竞争：

```bash
docker compose stop bac-anchor-app
docker compose exec -T bac-anchor-postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  < backups/bac-anchor-YYYYMMDD-HHMMSS.sql
docker compose start bac-anchor-app
```

如果是在仓库工作区内维护这套部署，也可以从仓库根目录使用 `tools/` 下的辅助脚本：

```bash
tools/deploy.sh
tools/logs.sh
tools/backup-postgres.sh
tools/restore-postgres.sh backups/bac-anchor-YYYYMMDD-HHMMSS.sql
```

## 反向代理

服务只 `expose` 8080，不直接映射公网端口。使用 Nginx Proxy Manager 时，把 upstream 指向：

```text
bac-anchor-app:8080
```

生产写入接口 `POST /api/v1/anchors` 需要 `Authorization: Bearer $BAC_ANCHOR_API_TOKEN`，管理页面 `/admin` 需要 `BAC_ANCHOR_ADMIN_TOKEN`。不要把 token 写入 `.bac` 文件或公开文档。

## 无域名联动测试

在本地建立 SSH 隧道：

```bash
ssh -N -L 18080:127.0.0.1:18080 rn3
```

仅在隔离测试目录中使用 `http://127.0.0.1:18080` 和 `--allow-insecure-anchor-url`。正式客户端必须使用可信公网 HTTPS；配置 DNS、反向代理和 TLS 前需获得明确授权。
