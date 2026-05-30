# DockerHub 发布流程

本流程用于从本地机器或自建发布机直接发布 BAC Anchor Server 的 DockerHub `linux/amd64` 镜像，不经过 GitHub Actions。

## 发布目标

- 默认镜像：`huangwb8/bensz-auto-contribution`
- 默认架构：`linux/amd64`
- 默认版本来源：`pyproject.toml` 的 `project.version`
- 默认 Dockerfile：`server/Dockerfile`

稳定版会发布这些标签：

```text
huangwb8/bensz-auto-contribution:x.y.z
huangwb8/bensz-auto-contribution:latest
huangwb8/bensz-auto-contribution:x.y
huangwb8/bensz-auto-contribution:x
```

预发布版只发布完整版本标签，例如 `1.3.0-rc.1`。

## 准备工作

安装并确认 Docker Buildx 可用：

```bash
docker buildx version
```

使用 DockerHub Access Token 登录，不要使用账号主密码：

```bash
docker login
```

发布前建议保持工作区干净，并确认 `pyproject.toml` 中的版本号就是要发布的版本。

## 推荐发布命令

先演练命令，不执行真实构建和推送：

```bash
make dockerhub-publish DRY_RUN=1
```

只在本地构建 `linux/amd64` 镜像，不推送：

```bash
make dockerhub-publish PUSH=0
```

发布到 DockerHub：

```bash
make dockerhub-publish
```

指定镜像仓库或版本：

```bash
make dockerhub-publish IMAGE=yourname/bensz-auto-contribution
make dockerhub-publish VERSION=1.2.0
```

## 脚本参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `IMAGE` | `huangwb8/bensz-auto-contribution` | DockerHub 镜像仓库 |
| `VERSION` | 读取 `pyproject.toml` | 镜像版本，不带 `v` 前缀 |
| `PUSH` | `1` | 为 `0` 时只本地构建 |
| `DRY_RUN` | `0` | 为 `1` 时只打印将执行的命令 |
| `FORCE` | `0` | 为 `1` 时允许覆盖已存在版本标签 |
| `SKIP_TESTS` | `0` | 为 `1` 时跳过 `python -m pytest -q` |
| `ALLOW_DIRTY` | `0` | 为 `1` 时允许在非干净工作区发布 |
| `VERIFY_PULL` | `1` | 推送后拉取镜像并做导入级 smoke test |

## 安全边界

- DockerHub token 只通过 `docker login` 进入 Docker 登录态，不写入仓库、`.env`、脚本参数或日志。
- 默认拒绝在脏工作区发布，避免把未确认改动打进正式镜像。
- 默认拒绝覆盖已存在的版本标签；确需重推时显式设置 `FORCE=1`。
- 发布后会执行 `docker buildx imagetools inspect`、`docker pull` 和 Python import smoke test，确认远端镜像可拉取且服务代码可加载。

## 回滚

生产部署应优先使用明确版本标签，例如 `huangwb8/bensz-auto-contribution:1.2.0`。如果新镜像有问题，回滚到上一个已验证版本标签；不要依赖重新构建旧代码覆盖历史标签。
