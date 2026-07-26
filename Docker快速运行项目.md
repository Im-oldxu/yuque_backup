# Yuque Backup Docker 快速运行项目

本文对应仓库中的 `deploy/Dockerfile`、`deploy/docker-compose.yaml` 和 `deploy/.env.example`。最终镜像只包含一个 `yuque-backup` 统一可执行文件；Compose 使用它的 `migrate`、`api` 和 `worker` 子命令。前端页面与 `/api/v1` 由同一个 API 容器同源提供。

项目使用 SQLite 持久化业务数据和任务队列，使用内容目录保存正文、标准化 Markdown 与附件，并使用导出目录保存可读 Markdown 树、任务快照和清单。PDF 由鉴权 API 按需生成，不持久化到导出卷。实际代码不依赖 PostgreSQL、Redis、Celery 或外部对象存储，因此 Compose 不包含这些服务。

## 1. 运行要求

- Docker Engine 24 或更新版本；
- Docker Compose v2，使用 `docker compose` 命令；
- Linux `amd64` 或 `arm64`，或者支持 Linux 容器的 Docker Desktop；
- 本地至少预留镜像、数据库和备份内容所需空间；
- 从公网使用时应有 HTTPS 反向代理。

### Release 二进制归档

每个 GitHub Release 除 Docker 镜像外，还提供以下由对应平台原生 runner 实际构建的统一可执行文件归档：

- `yuque-backup_<version>_linux_amd64.tar.gz`；
- `yuque-backup_<version>_linux_arm64.tar.gz`；
- `yuque-backup_<version>_windows_amd64.zip`；
- `checksums.txt`，包含上述三个归档的 SHA256。

归档名中的 `<version>` 不包含 Tag 的 `v` 前缀，例如 `v1.3.0` 对应 `yuque-backup_1.3.0_linux_amd64.tar.gz`。Release 标题使用项目显示名称和同一个无 `v` 版本号，例如 `Yuque Backup 1.3.0`。

Tag push 会同时触发普通 CI 和 Release Workflow；Release 会等待同一 Tag、同一提交的普通 CI 成功后才构建并发布这些产物，不会在 Release 阶段重复运行业务测试。

Linux 可下载全部归档和 `checksums.txt` 后执行 `sha256sum --check checksums.txt`；PowerShell 可使用 `Get-FileHash -Algorithm SHA256 <归档>` 与 `checksums.txt` 对照。二进制归档适用于自定义原生部署，但不会替代操作系统运行库：PDF 导出仍需要 WeasyPrint/Pango、中文字体等依赖。Docker 镜像已经按 `deploy/Dockerfile` 固定这些依赖，是推荐的生产部署方式。

原生部署必须继续使用同一个二进制的 `migrate`、`api` 和 `worker` 子命令分别运行迁移、API 和唯一 worker；从旧版升级后可执行一次 `export` 回填历史 Markdown。部署者需自行提供进程守护、权限、SQLite 本地磁盘目录、内容/导出目录和健康检查，不能只启动 API 进程。

所有命令默认在仓库根目录执行。先创建部署环境文件：

```bash
cp deploy/.env.example deploy/.env
```

PowerShell：

```powershell
Copy-Item deploy\.env.example deploy\.env
```

`deploy/.env` 已被 Git 忽略，不要提交真实主密钥。

## 2. 生成主密钥

`APP_MASTER_KEY` 用于 AES-256-GCM 加密语雀 Token。它必须解码为恰好 32 字节；每套部署只生成一次，并与数据备份分开保管。丢失或更换主密钥后，数据库中已有 Token 无法解密。

Linux/macOS 可使用 OpenSSL 生成 URL-safe Base64：

```bash
openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'
```

PowerShell：

```powershell
$bytes = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
```

把结果写入 `deploy/.env` 的 `APP_MASTER_KEY`。不要使用示例占位值。

## 3. 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YUQUE_BACKUP_IMAGE` | `ghcr.io/im-oldxu/yuque_backup:latest` | 当前仓库的小写 GHCR 地址；生产和回滚应固定为 `vX.Y.Z` |
| `APP_MASTER_KEY` | 无有效默认值 | 必填的 32 字节 URL-safe Base64 或 64 位 hex 主密钥 |
| `APP_ENV` | `development` | 本机 HTTP 快速运行使用 `development`；公网使用 `production` |
| `SECURE_COOKIES` | `false` | HTTPS 生产环境必须为 `true`；代码会拒绝 `production + false` |
| `TRUSTED_ORIGINS` | 本机 8000 端口 | 允许登录和写操作的完整浏览器 Origin，多个值用逗号分隔 |
| `TZ` | `Asia/Shanghai` | worker 额度等待和默认调度时区 |
| `BIND_ADDRESS` | `127.0.0.1` | 宿主机监听地址；反向代理同机部署时保持本机地址 |
| `APP_PORT` | `8000` | 宿主机端口，容器内固定为 8000 |

若把端口改为 `8877`，必须同步设置：

```dotenv
APP_PORT=8877
TRUSTED_ORIGINS=http://127.0.0.1:8877,http://localhost:8877
```

公网生产示例：

```dotenv
YUQUE_BACKUP_IMAGE=ghcr.io/im-oldxu/yuque_backup:v1.3.0
APP_ENV=production
SECURE_COOKIES=true
TRUSTED_ORIGINS=https://backup.example.com
BIND_ADDRESS=127.0.0.1
APP_PORT=8000
```

HTTPS 反向代理必须保留 `Host`、`Origin`、`Referer`、Cookie、`Set-Cookie`、`X-CSRF-Token`、`Idempotency-Key` 和 `X-Request-ID`。浏览器只能通过 `TRUSTED_ORIGINS` 中的 HTTPS 地址访问，不要同时暴露一个绕过 HTTPS 的公网端口。

## 4. 启动

先验证 Compose 插值和结构，再拉取镜像并启动：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml up -d
```

`migrate` 会先执行 Alembic 升级并正常退出；只有迁移成功后，`api` 和 `worker` 才会启动。查看状态：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml ps -a
```

预期 `migrate` 为 `Exited (0)`，`api` 和 `worker` 最终为 `healthy`。打开 `http://127.0.0.1:8000/#/auth` 创建唯一管理员。开发模式还可访问 `http://127.0.0.1:8000/docs`；生产模式关闭 OpenAPI 页面。

GHCR 镜像公开时不需要登录。若仓库或包被改为私有，先执行：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u '<GitHub 用户名>' --password-stdin
```

令牌至少需要读取包权限。

## 5. 日志和健康检查

持续查看 API 与 worker 日志：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml logs -f --tail=200 api worker
```

单独查看迁移日志：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml logs migrate
```

HTTP 检查：

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

`live` 只表示 API 进程存活；`ready` 还会检查数据库写入、迁移版本和内容目录。worker 容器的健康检查读取持久化心跳，超过代码规定的窗口会变为 unhealthy。日志会输出到 stdout/stderr，不在容器临时层保存业务日志。

容器以非 root 用户运行，根文件系统保持只读，并移除全部 Linux capabilities、启用 `no-new-privileges`。PyInstaller 单文件程序启动时必须把共享库解包到可执行文件系统，因此 Compose 只为 `/tmp` 提供 `256 MiB` 的 `exec,nosuid,nodev` tmpfs；其中不保存业务数据，容器停止后内容即消失。不要移除 `exec`，否则进程会因无法映射解包后的共享库而退出。

## 6. 停止和重启

安全停止但保留数据：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml down
```

重启现有服务：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml restart api worker
```

不要使用 `docker compose down -v`，它会删除数据库、内容和导出命名卷。不要手工删除 SQLite、`alembic_version` 或内容目录来处理启动失败。

## 7. 数据持久化和备份

Compose 创建三个具有固定名称的卷：

| 卷 | 容器路径 | 内容 | 访问方式 |
| --- | --- | --- | --- |
| `yuque-backup-database` | `/data/db` | SQLite、WAL 和迁移版本 | migrate/API/worker 读写 |
| `yuque-backup-content` | `/data/content` | 原始响应、正文、离线 HTML、附件和临时提交区 | API 只读、worker 读写 |
| `yuque-backup-exports` | `/data/exports` | 可读 Markdown `latest` 树、任务快照与清单 | worker 读写 |

列出并检查卷：

```bash
docker volume inspect yuque-backup-database yuque-backup-content yuque-backup-exports
```

一致性备份应先停止 API 和 worker，防止 SQLite WAL 与内容提交在归档期间变化：

```bash
mkdir -p backup
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml stop api worker
docker run --rm -v yuque-backup-database:/source:ro -v "$PWD/backup:/backup" alpine:3.22 tar -C /source -czf /backup/database.tgz .
docker run --rm -v yuque-backup-content:/source:ro -v "$PWD/backup:/backup" alpine:3.22 tar -C /source -czf /backup/content.tgz .
docker run --rm -v yuque-backup-exports:/source:ro -v "$PWD/backup:/backup" alpine:3.22 tar -C /source -czf /backup/exports.tgz .
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml start api worker
```

同时备份 `deploy/.env` 中的 `APP_MASTER_KEY`，但不要把它与公开归档放在一起。恢复前必须停止并移除应用容器，清空目标卷后分别解压数据库、内容与导出归档，再用归档对应的镜像版本启动。恢复会覆盖当前数据，操作前应再做一份当前卷快照。

SQLite 数据库必须位于宿主机本地文件系统，不能放在 NFS、SMB 或 NAS 上。内容卷可以绑定到已由宿主机挂载的 NAS，但必须验证原子重命名、长文件名、断线和剩余空间行为。绑定宿主机目录时，先创建目录并授权容器用户 `10001:10001`：

```bash
sudo mkdir -p /srv/yuque-backup/db /mnt/nas/yuque-backup/content /mnt/nas/yuque-backup/exports
sudo chown -R 10001:10001 /srv/yuque-backup/db /mnt/nas/yuque-backup/content /mnt/nas/yuque-backup/exports
```

可在自建的 Compose override 中把 `database` 绑定到 `/srv/yuque-backup/db`，把 `content` 和 `exports` 分别绑定到 `/mnt/nas/yuque-backup/content` 与 `/mnt/nas/yuque-backup/exports`。数据库路径仍必须是本地磁盘。

## 8. 从 v1.1.1 后端 Compose 迁移

v1.1.1 使用 `deploy/compose.backend.yaml` 和仓库根目录下的 `data/db`、`data/content` bind mount；v1.2.0 改为 `deploy/docker-compose.yaml` 和固定名称的 Docker 卷。旧数据不会被新命名卷自动发现，但仍保留在原目录中。首次切换前必须停止旧服务、备份数据并完成复制，不能直接启动空的新卷。

更新仓库前，先在旧版本目录停止服务：

```bash
docker compose -f deploy/compose.backend.yaml down
```

备份 `data/db`、`data/content` 和原部署使用的 `APP_MASTER_KEY`，再更新到 v1.2.0。随后在仓库根目录创建命名卷并复制旧数据：

```bash
docker volume create yuque-backup-database
docker volume create yuque-backup-content
docker run --rm --mount type=bind,src="$PWD/data/db",dst=/source,readonly --mount type=volume,src=yuque-backup-database,dst=/target alpine:3.22 sh -c 'cp -a /source/. /target/ && chown -R 10001:10001 /target'
docker run --rm --mount type=bind,src="$PWD/data/content",dst=/source,readonly --mount type=volume,src=yuque-backup-content,dst=/target alpine:3.22 sh -c 'cp -a /source/. /target/ && chown -R 10001:10001 /target'
```

复制完成后，使用原部署的同一个 `APP_MASTER_KEY` 配置 `deploy/.env`，把镜像固定为 `v1.2.0`，再按第 4 节启动。确认管理员、凭据、任务、版本和附件均可访问后再处置旧 `data/`；在确认前不要删除或移动旧目录。

## 9. 升级

发布工作流会为稳定 annotated Tag 发布 `vX.Y.Z` 和 `latest` 镜像；预发布 Tag 只发布自己的版本标签，不覆盖 `latest`。生产环境建议显式固定版本。

1. 阅读 GitHub Release 的版本说明。
2. 按第 7 节备份三个卷和主密钥。
3. 将 `deploy/.env` 的 `YUQUE_BACKUP_IMAGE` 改为目标 `vX.Y.Z`。
4. 拉取并重建容器：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml up -d
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml ps -a
```

升级时新的 `migrate` 容器先更新数据库；迁移失败会阻止 API 和 worker 启动。检查迁移日志和两个健康状态后，再恢复定时运维。

## 10. 回滚

镜像回滚和数据回滚必须一起评估。数据库迁移可能不向旧版本兼容，因此不要在新版本已执行迁移后只改回旧镜像。

1. 停止服务：`docker compose --env-file deploy/.env -f deploy/docker-compose.yaml down`。
2. 保存当前失败现场的数据库、内容卷和日志。
3. 恢复升级前的 `database.tgz`、`content.tgz` 与 `exports.tgz`，以及对应的 `APP_MASTER_KEY`。
4. 把 `YUQUE_BACKUP_IMAGE` 改回升级前的不可变 `vX.Y.Z`。
5. 执行 `pull`、`up -d`，检查迁移容器、API/worker 健康和日志。

如果 Release 明确说明该版本没有迁移且向后兼容，可以只回退镜像；没有明确说明时必须按数据快照回滚。

## 11. 常见问题

- `APP_MASTER_KEY must decode to exactly 32 bytes`：示例占位值未替换，或密钥不是 32 字节 URL-safe Base64/64 位 hex。
- `SECURE_COOKIES must be true in production`：`APP_ENV=production` 时必须启用安全 Cookie 并通过 HTTPS 访问。
- `database schema is not at the Alembic head`：检查 `migrate` 日志；不要绕过迁移直接启动 API。
- `unable to open database file`：检查命名卷或 bind mount 的所有者是否为 UID/GID `10001:10001`，并确认数据库位于本地磁盘。
- `/health/ready` 返回 503：检查数据库写权限、迁移 head 和内容目录挂载。
- 登录或写操作返回 CSRF/Origin 错误：`TRUSTED_ORIGINS` 必须与浏览器地址的协议、主机、端口完全一致。
- API healthy 但 worker unhealthy：查看 worker 日志、数据库写权限以及 `TZ`；不要启动多个 worker。
- 拉取 GHCR 失败：确认镜像地址全部小写，并在私有包场景登录 GHCR。
