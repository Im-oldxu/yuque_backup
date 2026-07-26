# Yuque Backup

Yuque Backup 是面向单管理员、自托管场景的语雀只读备份系统。v1.3.0 在多凭据增量备份、版本与附件保存、安全预览和离线浏览基础上，增加标准化 Markdown、按需 PDF 下载、可读导出快照、文章大纲与备份状态，并提供 Linux/Windows 原生发布归档。

## 项目结构

- `frontend/`：Vue 3、TypeScript、Vite/Rolldown 管理端，支持 Mock 和真实 API 模式；
- `backend/`：FastAPI API、独立 worker、SQLite、Alembic 和本地内容存储；
- `deploy/`：前后端统一可执行文件的多阶段 Dockerfile、Compose 与环境变量示例；
- `需求文档.md`、`技术方案文档.md`、`API 接口文档.md`：v1.3.0 产品、技术和接口契约；
- `CHANGE/`：每个 SemVer Tag 对应的独立发布说明。

## 快速开始

Docker 一键运行、日志、升级、回滚和数据持久化见 [Docker快速运行项目.md](Docker快速运行项目.md)。默认镜像为 `ghcr.io/im-oldxu/yuque_backup:latest`，同一镜像中的统一可执行文件以不同子命令运行迁移、API 和唯一 worker。

后端本地开发、密钥、迁移、API/worker、测试和单文件构建说明见 [后端快速启动说明.md](后端快速启动说明.md)。

前端安装、Mock/真实 API 模式、测试和构建说明见 [前端快速启动说明.md](前端快速启动说明.md)。联调时先启动后端 API 和唯一 worker，再执行：

```powershell
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm dev:real
```

默认访问地址为 `http://127.0.0.1:3002/#/auth`，后端开发文档为 `http://127.0.0.1:8000/docs`。

## v1.3.0 交付基线

- 契约：OpenAPI 50 条路径、56 个操作，数据库迁移 head 为 `0002`；
- 数据：每个版本保存标准化 `export.md`，worker 维护 `/data/exports/latest` 和带清单的任务快照；升级后可运行 `yuque-backup export` 回填历史版本；
- 阅读：浏览器使用经过 DOMPurify 清理的 Markdown、Shiki 本地代码高亮、响应式文章大纲和备份状态，支持 Markdown/PDF 下载；
- 交付：GitHub Release 在同一 Tag/提交的 CI 成功后发布 Linux amd64/arm64、Windows amd64 归档和 `checksums.txt`，并发布多架构 Docker 镜像。

## 范围

本项目只从语雀读取和保存数据，不提供语雀写回、双向同步、多用户/RBAC、全文搜索、Webhook、S3/WebDAV 或 AI/RAG。完整边界以 [需求文档.md](需求文档.md) 为准。
