# Yuque Backup

Yuque Backup 是面向单管理员、自托管场景的语雀只读备份系统。v1.2.0 提供多凭据管理与编辑、知识库发现与选择、带额度预估的手动/定时备份向导、持久化备份队列、增量版本与附件保存、安全预览、实时任务活动、删除保留及本地离线浏览，并增加前后端统一可执行文件与一体化 Docker 发布。

## 项目结构

- `frontend/`：Vue 3、TypeScript、Vite/Rolldown 管理端，支持 Mock 和真实 API 模式；
- `backend/`：FastAPI API、独立 worker、SQLite、Alembic 和本地内容存储；
- `deploy/`：前后端统一可执行文件的多阶段 Dockerfile、Compose 与环境变量示例；
- `需求文档.md`、`技术方案文档.md`、`API 接口文档.md`：v1.2.0 产品、技术和接口契约。

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

## v1.2.0 验证基线

- 后端：Ruff、Mypy、`uv lock --check` 和 118 个 Pytest 测试通过；
- 前端：17 个 Vitest 测试文件、61 个测试、Vue/TypeScript 类型检查和生产构建通过；
- 契约：OpenAPI 47 条路径、53 个操作；
- 交付：Windows 单文件程序已通过版本、迁移、嵌入式首页、初始化 API、API ready 和 worker 心跳冒烟验证；CI 同步覆盖 Linux 单文件程序、容器镜像和 Compose。

## 范围

本项目只从语雀读取和保存数据，不提供语雀写回、双向同步、多用户/RBAC、全文搜索、Webhook、S3/WebDAV 或 AI/RAG。完整边界以 [需求文档.md](需求文档.md) 为准。
