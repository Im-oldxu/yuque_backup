# Yuque Backup

Yuque Backup 是面向单管理员、自托管场景的语雀只读备份系统。v1.0.0 提供多凭据管理、知识库发现与选择、持久化备份队列、定时增量备份、版本和资源保存、安全预览、任务审计、删除保留及本地离线浏览。

## 项目结构

- `frontend/`：Vue 3、TypeScript、Vite/Rolldown 管理端，支持 Mock 和真实 API 模式；
- `backend/`：FastAPI API、独立 worker、SQLite、Alembic 和本地内容存储；
- `deploy/`：后端 Dockerfile 与 Docker Compose；
- `需求文档.md`、`技术方案文档.md`、`API 接口文档.md`：v1.0.0 产品、技术和接口契约。

## 快速开始

后端安装、密钥、迁移、API/worker、测试和 Docker Compose 说明见 [后端快速启动说明.md](后端快速启动说明.md)。

前端安装、Mock/真实 API 模式、测试和构建说明见 [前端快速启动说明.md](前端快速启动说明.md)。联调时先启动后端 API 和唯一 worker，再执行：

```powershell
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm dev:real
```

默认访问地址为 `http://127.0.0.1:3002/#/auth`，后端开发文档为 `http://127.0.0.1:8000/docs`。

## v1.0.0 验证基线

- 后端：Ruff、Mypy、99 个 Pytest 测试、Alembic head/check、wheel 和 sdist 构建通过；
- 前端：11 个 Vitest 测试文件、46 个测试、类型检查和生产构建通过；
- 契约：OpenAPI 46 条路径、52 个操作；
- 运行：API live/ready、worker 心跳、真实 API 初始化/登录/退出、Cookie/CSRF、桌面和移动端渲染通过。

## 范围

本项目只从语雀读取和保存数据，不提供语雀写回、双向同步、多用户/RBAC、全文搜索、Webhook、S3/WebDAV 或 AI/RAG。完整边界以 [需求文档.md](需求文档.md) 为准。
