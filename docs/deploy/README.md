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

生成生产密钥和 token 后填入 `.env`：

```bash
python - <<'PY'
import base64
import secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

private_key = Ed25519PrivateKey.generate()
private_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
print("BAC_ANCHOR_PRIVATE_KEY_B64=" + base64.b64encode(private_bytes).decode("ascii"))
print("BAC_ANCHOR_API_TOKEN=" + secrets.token_urlsafe(48))
print("BAC_ANCHOR_ADMIN_TOKEN=" + secrets.token_urlsafe(48))
print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(32))
PY
```

`BAC_ANCHOR_RELEASE_VERSION` 应填写已经发布到 DockerHub 的版本号，例如 `1.2.2`。
这套部署使用 PostgreSQL 和 Redis，需要镜像版本包含服务端 PostgreSQL/Redis 支持；从源码部署时可取消 `docker-compose.yml` 中 `build` 配置的注释后本地构建。

## 启动或更新

```bash
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
```

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
