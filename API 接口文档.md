# Yuque-Backup MVP API 接口文档

> 文档状态：已确认并完成实现，作为 v1.1.1 前后端契约\
> 版本：v1.1.1\
> 编写及官方文档核验日期：2026-07-23  
> 实现与契约验收日期：2026-07-26\
> 输入依据：[需求文档.md](需求文档.md)、[技术方案文档.md](技术方案文档.md)  
> 适用对象：Yuque-Backup 前端与后端开发人员

## 1. 范围与约定

本文只定义 MVP 所需的本地 Web API。语雀 OpenAPI 是 worker 的内部集成，不由前端直接调用，也不在本文重复定义。

v1.1.1 后端 OpenAPI 共包含 47 条路径、53 个操作；前端真实 API 模式已经完成初始化、登录、会话、CSRF、仪表盘和 worker 心跳的浏览器联调。接口路径、字段和错误结构仍以本文契约为准。

MVP API 覆盖：

- 初始化、登录、退出、当前管理员和修改密码；
- 语雀凭据新增、更新、验证、启停、删除和知识库发现；
- 知识库选择、主凭据、目录树、文档搜索和浏览；
- 文档版本、资源状态、安全预览和单项下载；
- 手动备份、备份额度预估、任务取消、重新执行、任务/子任务/问题查询；
- 仪表盘、Cron/时区、保留期、资源上限和存储信息；
- 删除墓碑查询；
- 健康检查和异步操作查询。

不提供语雀写回、整库 ZIP、全文搜索、评论、Webhook、通知、多用户/RBAC、S3/WebDAV、AI/RAG 或其他非 MVP 接口。

### 1.1 基础地址与数据格式

| 项目 | 约定 |
| --- | --- |
| API 前缀 | `/api/v1` |
| 传输 | 生产环境 HTTPS；前端与 API 同源 |
| JSON | `application/json; charset=utf-8` |
| 字段命名 | `snake_case` |
| 本地资源 ID | UUID 字符串；前端不得解析其结构 |
| 语雀 ID | 单独使用 `yuque_*_id` 字段，不能与本地 ID 混用 |
| 时间 | ISO 8601，响应统一为 UTC，例如 `2026-07-23T14:30:00Z` |
| 布尔值 | JSON `true/false` |
| 空值 | 仅在字段明确允许时使用 `null` |
| 未知请求字段 | 拒绝并返回 `422 VALIDATION_ERROR` |

成功响应直接返回资源对象；列表使用统一分页对象，不再包一层通用 `data`。所有响应携带 `X-Request-ID`，客户端报错时应记录该值。

每个接口条目统一按以下信息阅读：标题是接口说明；紧随其后的 `GET/POST/PUT/PATCH/DELETE` 是请求方式和接口路径；“权限”说明会话与 CSRF 要求；“Query/Header/请求”定义请求参数；“成功/响应”定义响应数据和 HTTP 状态；“错误”列出该接口的主要错误码。未重复列出的通用错误仍按第 15 节处理。

### 1.2 认证与 CSRF

- 登录后后端设置 `yb_session` Cookie：`HttpOnly`、`SameSite=Lax`、`Path=/`，HTTPS 时必须带 `Secure`；
- 数据库只保存会话令牌哈希；前端不能读取或持久化会话令牌；
- 后端同时设置可由同源前端读取的 `yb_csrf` Cookie；
- 所有带管理员会话的 `POST`、`PUT`、`PATCH`、`DELETE` 请求必须将该值放入 `X-CSRF-Token`；
- 初始化和登录没有既有会话，不要求 CSRF Header，但必须是 `application/json` 且通过同源 `Origin/Referer` 校验；
- 未登录或会话过期返回 `401 AUTH_REQUIRED`；CSRF 校验失败返回 `403 CSRF_INVALID`；
- 生产环境不启用跨域 CORS，不支持把 Cookie 交给第三方站点。

### 1.3 分页、筛选与排序

列表接口统一支持：

| 参数 | 类型 | 默认值 | 约束 |
| --- | --- | --- | --- |
| `page` | integer | `1` | `>=1` |
| `page_size` | integer | `20` | `1..100` |

分页响应：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

各列表的默认排序在接口说明中固定。无效排序或筛选值返回 `422`。MVP 不提供任意字段排序表达式。

### 1.4 PATCH 语义

- 请求中未出现的字段保持不变；
- `null` 只用于接口明确允许清空的字段；
- 空字符串不等同于 `null`；
- 后端通过 Pydantic v2 的已设置字段集合区分“未提供”和“显式传 null”。

### 1.5 幂等性

以下接口要求 `Idempotency-Key` Header，值为客户端生成的 UUID：

- `POST /system/initialize`；
- `POST /backup-jobs`；
- `POST /backup-jobs/{job_id}/rerun`。

同一管理员、同一路径和同一 Key 的重复请求返回首次结果。Key 相同但请求体不同返回 `409 IDEMPOTENCY_CONFLICT`。服务端至少保留幂等记录 24 小时。

### 1.6 异步操作

凭据验证和知识库发现会访问语雀，必须经过持久化队列，因此返回 `202 Accepted` 和带 `id` 的 Operation，不能在 API 进程中同步调用语雀。

```json
{
  "id": "95f8ba9c-7cdf-4a30-8f10-8eaa780a69ad",
  "type": "credential_verify",
  "status": "queued",
  "created_at": "2026-07-23T14:30:00Z"
}
```

前端每 2 至 3 秒查询操作状态，进入终态后停止。备份任务使用 `backup_job` 自身状态，不使用该通用操作对象。

## 2. 状态枚举

API 使用稳定英文值，前端映射为中文展示文案，不直接显示枚举值。

### 2.1 任务状态

| API 值 | 中文 | 终态 |
| --- | --- | --- |
| `queued` | 排队中 | 否 |
| `running` | 运行中 | 否 |
| `waiting_quota` | 等待额度 | 否 |
| `succeeded` | 成功 | 是 |
| `partial` | 部分成功 | 是 |
| `failed` | 失败 | 是 |
| `cancelled` | 已取消 | 是 |

总任务只有在所有未完成工作均等待额度时才为 `waiting_quota`；若其他凭据仍在运行，总任务保持 `running`。

### 2.2 凭据状态

| API 值 | 中文 |
| --- | --- |
| `unverified` | 未验证 |
| `valid` | 有效 |
| `waiting_quota` | 等待额度 |
| `action_required` | 需要处理 |
| `disabled` | 已停用 |

### 2.3 版本完整性

| API 值 | 中文 | 可作为最新可浏览版本 |
| --- | --- | --- |
| `complete` | 完整 | 是 |
| `partial` | 部分成功 | 是，必须显示告警 |
| `failed` | 失败 | 否 |

### 2.4 异步操作状态

`queued`、`running`、`waiting_quota`、`succeeded`、`failed`、`cancelled`。终态为 `succeeded`、`failed`、`cancelled`。

### 2.5 其他枚举

| 类型 | 值 |
| --- | --- |
| 凭据主体 | `user`、`group`、`unknown` |
| 文档类型 | `Doc`、`Sheet`、`Thread`、`Board`、`Table`、`HtmlDoc`、`unknown` |
| 任务触发方式 | `manual`、`cron` |
| 任务范围 | `all`、`credential`、`repository`、`repositories` |
| 资源状态 | `pending`、`downloaded`、`skipped`、`failed` |
| 问题级别 | `warning`、`error` |

## 3. 通用响应模型

### 3.1 错误响应

```json
{
  "code": "VALIDATION_ERROR",
  "message": "请求参数不合法",
  "request_id": "req_01K0...",
  "field_errors": [
    {
      "field": "password",
      "reason": "min_length"
    }
  ]
}
```

| 字段 | 类型 | 必有 | 说明 |
| --- | --- | --- | --- |
| `code` | string | 是 | 稳定机器错误码 |
| `message` | string | 是 | 可安全显示的中文摘要，不含堆栈和敏感数据 |
| `request_id` | string | 是 | 与响应头一致 |
| `field_errors` | array | 否 | 仅参数校验错误返回 |
| `retry_after_seconds` | integer | 否 | 本地限速时返回 |

### 3.2 异步操作 Operation

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 操作 ID |
| `type` | string | `credential_verify` 或 `repository_discovery` |
| `status` | enum | 异步操作状态 |
| `credential_id` | UUID | 关联凭据 |
| `result` | object/null | 成功后的安全摘要 |
| `error` | object/null | 失败后的稳定错误码和摘要 |
| `next_retry_at` | datetime/null | 等待额度时的下次尝试时间 |
| `created_at` | datetime | 创建时间 |
| `started_at` | datetime/null | 开始时间 |
| `finished_at` | datetime/null | 结束时间 |

`Operation` 是持久化队列项的安全投影，不返回内部租约、请求头、Token、响应正文或堆栈。

## 4. 健康与初始化

### 4.1 存活检查

`GET /health/live`

- 权限：公开；
- 响应：`200`；不访问数据库和外网。

```json
{
  "status": "ok"
}
```

### 4.2 就绪检查

`GET /health/ready`

- 权限：公开；只返回总状态，不泄露路径、版本或异常详情；
- `200`：数据库版本正确且数据目录可用；
- `503 SERVICE_UNAVAILABLE`：尚未就绪。

```json
{
  "status": "ready"
}
```

### 4.3 查询初始化状态

`GET /api/v1/system/initialization`

- 权限：公开；
- 响应：`200`。

```json
{
  "initialized": false
}
```

### 4.4 创建唯一管理员

`POST /api/v1/system/initialize`

- 权限：仅系统未初始化时；同源校验；
- Header：`Idempotency-Key` 必填；
- 请求：

```json
{
  "username": "admin",
  "password": "a-strong-password"
}
```

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `username` | string | 去除首尾空格后 3..64 字符 |
| `password` | string | 12..128 字符，不自动去空格 |

- 成功：`201`，创建会话并设置 Cookie；响应体同“当前管理员”；
- 错误：`409 INITIALIZATION_ALREADY_COMPLETED`、`409 IDEMPOTENCY_CONFLICT`、`422 VALIDATION_ERROR`。

并发初始化时只有第一个有效请求成功，其余返回 `409`。

## 5. 认证

### 5.1 登录

`POST /api/v1/auth/login`

- 权限：公开；同源校验；
- 请求：

```json
{
  "username": "admin",
  "password": "a-strong-password"
}
```

- 成功：`200`，设置 `yb_session` 和 `yb_csrf` Cookie；
- 错误：`401 INVALID_CREDENTIALS`、`429 LOGIN_RATE_LIMITED`、`422 VALIDATION_ERROR`；
- 不得通过错误文案区分用户名不存在或密码错误。

### 5.2 当前管理员

`GET /api/v1/auth/me`

- 权限：管理员会话；
- 响应：`200`。

```json
{
  "id": "2d78345d-a7df-4f3c-9418-27e578882342",
  "username": "admin",
  "created_at": "2026-07-23T14:30:00Z",
  "password_changed_at": null
}
```

### 5.3 退出

`POST /api/v1/auth/logout`

- 权限：管理员会话 + CSRF；
- 成功：`204`，撤销当前服务端会话并清除两个 Cookie；
- 重复退出时，前端将 `401` 视为已退出。

### 5.4 修改密码

`PUT /api/v1/auth/password`

- 权限：管理员会话 + CSRF；
- 请求：

```json
{
  "current_password": "old-password",
  "new_password": "new-strong-password"
}
```

- 成功：`204`；保留当前会话，撤销其他会话；
- 错误：`400 CURRENT_PASSWORD_INCORRECT`、`422 VALIDATION_ERROR`。

## 6. 异步操作

### 6.1 查询操作

`GET /api/v1/operations/{operation_id}`

- 权限：管理员会话；
- 成功：`200 Operation`；
- 错误：`404 OPERATION_NOT_FOUND`。

该接口只查询验证和发现操作；备份进度使用任务接口。

## 7. 语雀凭据

### 7.1 凭据响应模型

```json
{
  "id": "8d6d6254-23f1-47f8-bb84-f27720d34d26",
  "name": "个人语雀",
  "base_url": "https://www.yuque.com",
  "token_masked": "************Ab3x",
  "subject_type": "user",
  "subject_id": "12345",
  "login": "example",
  "status": "valid",
  "enabled": true,
  "last_verified_at": "2026-07-23T14:30:00Z",
  "rate_limit": {
    "limit": 5000,
    "remaining": 4932,
    "observed_at": "2026-07-23T14:30:00Z"
  },
  "next_retry_at": null,
  "active_operation_id": null,
  "repository_count": 8,
  "created_at": "2026-07-23T14:20:00Z",
  "updated_at": "2026-07-23T14:30:00Z"
}
```

完整 Token 永不出现在任何响应中。无法取得额度 Header 时，`rate_limit` 为 `null`。
`active_operation_id` 在验证或发现操作处于非终态时返回，页面刷新后可据此恢复轮询；没有活动操作时为 `null`。

### 7.2 凭据列表

`GET /api/v1/credentials`

- 权限：管理员会话；
- Query：`page`、`page_size`、可选 `status`、可选 `enabled`；
- 默认排序：`created_at ASC`；
- 成功：`200`，分页 `Credential`。

### 7.3 新增并验证凭据

`POST /api/v1/credentials`

- 权限：管理员会话 + CSRF；
- 请求：

```json
{
  "name": "个人语雀",
  "base_url": "https://www.yuque.com",
  "token": "secret-token"
}
```

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `name` | string | 1..100 字符 |
| `base_url` | URL | 仅 HTTPS，必须为 origin，不允许 path/query/fragment |
| `token` | string | 1..2048 字符，不回显 |

- 成功接收：`202`；在一个事务中保存加密候选记录并创建 `credential_verify` 操作，初始 `status=unverified`、`enabled=false`；

```json
{
  "credential": {
    "id": "8d6d6254-23f1-47f8-bb84-f27720d34d26",
    "name": "个人语雀",
    "base_url": "https://www.yuque.com",
    "token_masked": "************Ab3x",
    "status": "unverified",
    "enabled": false
  },
  "operation": {
    "id": "95f8ba9c-7cdf-4a30-8f10-8eaa780a69ad",
    "type": "credential_verify",
    "status": "queued"
  }
}
```

- 错误：`409 CREDENTIAL_NAME_EXISTS`、`422 INVALID_BASE_URL`、`422 VALIDATION_ERROR`。

API 进程不直接访问语雀；worker 从持久化队列执行验证。验证失败时保留候选凭据并标为 `action_required`，便于管理员修改后重试。

### 7.4 凭据详情

`GET /api/v1/credentials/{credential_id}`

- 权限：管理员会话；
- 成功：`200 Credential`；
- 错误：`404 CREDENTIAL_NOT_FOUND`。

### 7.5 更新凭据

`PATCH /api/v1/credentials/{credential_id}`

- 权限：管理员会话 + CSRF；
- 请求字段：`name`、`base_url`、`token`，至少一个；字段约束同新增；
- Token 或基础域名变化后，状态重置为 `unverified`、`enabled=false`，必须重新验证；
- 仅修改名称不改变验证状态；
- 成功：`200 Credential`；
- 错误：`404 CREDENTIAL_NOT_FOUND`、`409 CREDENTIAL_NAME_EXISTS`、`422 VALIDATION_ERROR`。

### 7.6 重新验证凭据

`POST /api/v1/credentials/{credential_id}/verify`

- 权限：管理员会话 + CSRF；
- 请求体：无；
- 成功接收：`202 Operation`，`type=credential_verify`；
- 错误：`404 CREDENTIAL_NOT_FOUND`、`409 OPERATION_ALREADY_RUNNING`。

若该凭据已有 `waiting_quota` 的验证操作，再次调用本接口不会创建重复 Operation，而是立即唤醒原队列项执行一次额度探测。该探测只绕过一次本地等待窗口；若语雀仍返回 429，worker 会等待到本地时区的下一日再自动重试，当天不再自动探测。已有操作处于 `queued` 或 `running` 时仍返回 `409 OPERATION_ALREADY_RUNNING`。

worker 通过统一队列调用语雀 `GET /api/v2/user`。验证结果映射：

| 语雀结果 | 凭据状态 | Operation |
| --- | --- | --- |
| 2xx | `valid` | `succeeded` |
| 429 | `waiting_quota` | `waiting_quota`，下一日自动续跑 |
| 401/403 | `action_required` | `failed` |
| 域名/网络/5xx 重试耗尽 | `action_required` | `failed` |

### 7.7 发现知识库

`POST /api/v1/credentials/{credential_id}/discover-repositories`

- 权限：管理员会话 + CSRF；
- 前置：凭据已验证且未停用；
- 请求体：无；
- 成功接收：`202 Operation`，`type=repository_discovery`；
- 成功结果示例：

```json
{
  "discovered": 8,
  "created": 2,
  "updated": 6,
  "duplicates": 1,
  "requires_primary_selection": 1
}
```

- 错误：`404 CREDENTIAL_NOT_FOUND`、`409 CREDENTIAL_NOT_VALID`、`409 OPERATION_ALREADY_RUNNING`。

新发现知识库默认选中；同域名和 `book_id` 只创建一份知识库。

### 7.8 启用凭据

`POST /api/v1/credentials/{credential_id}/enable`

- 权限：管理员会话 + CSRF；
- 前置：`status=valid`；
- 成功：`200 Credential`；
- 错误：`404 CREDENTIAL_NOT_FOUND`、`409 CREDENTIAL_NOT_VALID`。

### 7.9 停用凭据

`POST /api/v1/credentials/{credential_id}/disable`

- 权限：管理员会话 + CSRF；
- 成功：`200 Credential`，不再发出请求；
- 已有备份、任务和知识库不删除；无其他凭据可用的知识库显示连接已停用。

### 7.10 删除凭据

`DELETE /api/v1/credentials/{credential_id}`

- 权限：管理员会话 + CSRF；
- 成功：`204`；逻辑删除凭据并停止其队列，不删除历史数据；
- 错误：`404 CREDENTIAL_NOT_FOUND`。

前端删除前应使用凭据详情中的 `repository_count` 提示影响范围。

## 8. 知识库与目录

### 8.1 知识库响应模型

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 本地知识库 ID |
| `yuque_book_id` | string | 语雀知识库 ID |
| `base_url` | URL | 规范化语雀域名 |
| `name` | string | 名称 |
| `slug` | string/null | 语雀 slug |
| `namespace` | string/null | 原语雀路径 |
| `selected` | boolean | 是否纳入自动备份 |
| `connection_status` | string | `connected`、`disabled`、`action_required` |
| `primary_credential_id` | UUID/null | 主凭据；重复发现且未选择时可为 null |
| `credential_count` | integer | 可访问凭据数 |
| `document_count` | integer | 当前本地文档数 |
| `last_success_at` | datetime/null | 最近成功备份时间 |
| `content_updated_at` | datetime/null | 语雀元数据时间 |

### 8.2 知识库列表

`GET /api/v1/repositories`

- 权限：管理员会话；
- Query：`page`、`page_size`、可选 `q`、`selected`、`connection_status`、`credential_id`；
- `q` 只匹配知识库名称和 namespace；
- 默认排序：`name ASC, id ASC`；
- 成功：`200`，分页 `Repository`。

### 8.3 知识库详情

`GET /api/v1/repositories/{repository_id}`

- 权限：管理员会话；
- 成功：`200 Repository`，额外包含可访问凭据摘要；
- 错误：`404 REPOSITORY_NOT_FOUND`。

### 8.4 选择或排除知识库

`PATCH /api/v1/repositories/{repository_id}/selection`

- 权限：管理员会话 + CSRF；
- 请求：

```json
{
  "selected": false
}
```

- 成功：`200 Repository`；取消选择只影响未来任务，不删除已有内容。

### 8.5 指定主凭据

`PUT /api/v1/repositories/{repository_id}/primary-credential`

- 权限：管理员会话 + CSRF；
- 请求：

```json
{
  "credential_id": "8d6d6254-23f1-47f8-bb84-f27720d34d26"
}
```

- 成功：`200 Repository`；
- 错误：`404 REPOSITORY_NOT_FOUND`、`404 CREDENTIAL_NOT_FOUND`、`409 CREDENTIAL_CANNOT_ACCESS_REPOSITORY`。

备用凭据不会自动发请求；主凭据失效时要求管理员显式切换。

### 8.6 获取目录树

`GET /api/v1/repositories/{repository_id}/toc`

- 权限：管理员会话；
- 成功：`200`，按官方顺序返回嵌套树；
- 错误：`404 REPOSITORY_NOT_FOUND`。

```json
{
  "repository_id": "...",
  "updated_at": "2026-07-23T14:30:00Z",
  "items": [
    {
      "id": "...",
      "type": "DOC",
      "title": "项目说明",
      "document_id": "...",
      "path": "/项目说明",
      "children": []
    }
  ]
}
```

失效目录节点允许 `document_id=null`，前端显示不可用状态，不应崩溃。

## 9. 文档、版本与资源

### 9.1 文档列表与搜索

`GET /api/v1/documents`

- 权限：管理员会话；
- Query：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `page`、`page_size` | integer | 通用分页 |
| `q` | string | 可选；匹配文档标题、slug、当前/删除前语雀路径，不搜索正文 |
| `repository_id` | UUID | 可选知识库 |
| `toc_item_id` | UUID | 可选目录节点 |
| `deleted` | boolean | 可选删除状态 |
| `completeness` | enum | 可选最新版本完整性 |

- 默认排序：`title ASC, id ASC`；
- 成功：`200`，分页文档摘要。

文档摘要至少包含：`id`、`repository_id`、`yuque_doc_id`、`type`、`title`、`slug`、`path`、`deleted_at`、`purge_at`、`latest_version_id`、`latest_version_completeness`、`updated_at`。

### 9.2 全局搜索

`GET /api/v1/search`

- 权限：管理员会话；
- Query：`q` 必填，1..200 字符；可选 `repository_id`、`deleted`；
- 响应最多各返回 20 个知识库和文档结果，不搜索正文；
- 成功：`200`。

```json
{
  "repositories": [],
  "documents": []
}
```

### 9.3 文档详情

`GET /api/v1/documents/{document_id}`

- 权限：管理员会话；
- 成功：`200`；
- 错误：`404 DOCUMENT_NOT_FOUND`。

详情在摘要基础上增加：知识库摘要、目录路径、原路径、删除状态、剩余保留秒数、最新成功版本摘要和版本数量。

### 9.4 历史版本列表

`GET /api/v1/documents/{document_id}/versions`

- 权限：管理员会话；
- Query：`page`、`page_size`；
- 默认排序：`created_at DESC, id DESC`；
- 成功：`200`，分页版本摘要。

版本摘要字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 本地版本 ID |
| `remote_version_id` | string/null | 语雀远端版本 ID |
| `format` | string/null | `markdown/html/lake/lakesheet` 等 |
| `content_hash` | string | 规范化版本哈希 |
| `completeness` | enum | 完整性 |
| `is_latest` | boolean | 是否为最新可浏览版本 |
| `resource_total` | integer | 计划下载的显式附件数 |
| `resource_downloaded` | integer | 已保存的显式附件数 |
| `issue_count` | integer | 问题数 |
| `source_job_id` | UUID | 来源任务 |
| `remote_updated_at` | datetime/null | 语雀时间 |
| `created_at` | datetime | 本地提交时间 |

失败版本可以作为审计记录返回，但 `is_latest=false`、`preview_available=false`，且不能更新文档的最新成功版本指针；具体失败原因通过来源任务的问题接口查询。

### 9.5 版本详情

`GET /api/v1/documents/{document_id}/versions/{version_id}`

- 权限：管理员会话；
- 成功：`200`；包含版本摘要、标准化元数据、资源状态和下载可用性；
- 错误：`404 VERSION_NOT_FOUND`。

```json
{
  "id": "...",
  "document_id": "...",
  "completeness": "partial",
  "preview_available": true,
  "downloads": {
    "raw_response": true,
    "raw_body": true,
    "offline_html": true
  },
  "asset_summary": {
    "total": 3,
    "downloaded": 2,
    "failed": 1,
    "skipped": 0
  },
  "issue_count": 1
}
```

安全 URL 仅在需要展示问题时返回，必须移除查询中的敏感参数；原始 URL 不返回前端。

版本详情中的资源统计只包含文档明确标识的附件。Markdown 图片、HTML 图片、结构化 `src` 以及仅因路径带文件扩展名而看似文件的普通外部链接不计入资源统计，也不触发下载。

### 9.6 版本资源列表

`GET /api/v1/documents/{document_id}/versions/{version_id}/assets`

- 权限：管理员会话；
- Query：`page`、`page_size`、可选 `status`、`type`；
- 默认排序：正文出现顺序、`id ASC`；
- 成功：`200`，分页资源引用；
- 资源字段：`asset_id`、名称、资源类型、MIME、大小、状态、是否可内联、是否可下载和安全问题码；
- 错误：`404 VERSION_NOT_FOUND`。

该列表只返回文档明确标识的附件。Markdown/HTML 图片和普通外部文件链接保留在原始正文中，不作为版本资源返回。

同一物理资源在不同版本中可以重复出现为不同引用，但下载仍通过全局 `asset_id` 去重。

### 9.7 版本问题列表

`GET /api/v1/documents/{document_id}/versions/{version_id}/issues`

- 权限：管理员会话；
- Query：`page`、`page_size`、可选 `level`、`code`；
- 默认排序：`last_occurred_at DESC, id DESC`；
- 成功：`200`，分页安全问题摘要；
- 错误：`404 VERSION_NOT_FOUND`。

### 9.8 安全预览

`GET /api/v1/documents/{document_id}/versions/{version_id}/preview`

- 权限：管理员会话；
- 成功：`200 text/html; charset=utf-8`；
- Header：严格 `Content-Security-Policy`、`X-Content-Type-Options: nosniff`、`Cache-Control: private, no-store`；
- 前端必须放入无 `allow-scripts`、无 `allow-same-origin` 的 sandbox iframe；
- 错误：`404 VERSION_NOT_FOUND`、`409 PREVIEW_NOT_AVAILABLE`、`410 VERSION_CONTENT_PURGED`。

原始 Markdown 中的图片链接保持不变；安全预览不下载或加载这些远程图片。该 HTML 只能引用本系统受控资源地址，不主动加载远程 URL。

### 9.9 下载原始 API 响应

`GET /api/v1/documents/{document_id}/versions/{version_id}/downloads/raw-response`

- 权限：管理员会话；
- 成功：`200 application/json`，`Content-Disposition: attachment`；
- 错误：`404 VERSION_NOT_FOUND`、`410 VERSION_CONTENT_PURGED`。

### 9.10 下载原始正文

`GET /api/v1/documents/{document_id}/versions/{version_id}/downloads/raw-body`

- 权限：管理员会话；
- 成功：`200` 流式响应，文件扩展名和 MIME 按保存格式确定；
- 错误：`404 VERSION_NOT_FOUND`、`410 VERSION_CONTENT_PURGED`。

### 9.11 下载离线 HTML

`GET /api/v1/documents/{document_id}/versions/{version_id}/downloads/offline-html`

- 权限：管理员会话；
- 成功：`200 text/html`，`Content-Disposition: attachment`；
- HTML 中资源路径已本地化，但单独脱离服务器打开时不承诺附件内嵌；
- 错误：`404 VERSION_NOT_FOUND`、`409 PREVIEW_NOT_AVAILABLE`、`410 VERSION_CONTENT_PURGED`。

### 9.12 内联读取资源

`GET /api/v1/assets/{asset_id}/content`

- 权限：管理员会话；仅供安全预览引用；
- 成功：`200` 流式响应，允许安全图片等可内联 MIME；
- 非安全内联类型强制 `Content-Disposition: attachment`；
- 错误：`404 ASSET_NOT_FOUND`、`410 ASSET_CONTENT_PURGED`。

### 9.13 下载单个资源

`GET /api/v1/assets/{asset_id}/download`

- 权限：管理员会话；
- 成功：`200` 流式响应，使用安全文件名和 `Content-Disposition: attachment`，支持 Range；
- 错误：`404 ASSET_NOT_FOUND`、`410 ASSET_CONTENT_PURGED`。

所有下载路径均由数据库 ID 定位，不能接受任意磁盘路径或远程 URL。

## 10. 备份任务

### 10.1 创建手动任务

`POST /api/v1/backup-jobs`

- 权限：管理员会话 + CSRF；
- Header：`Idempotency-Key` 必填；
- 请求四选一：

```json
{ "scope": { "type": "all" } }
```

```json
{
  "scope": {
    "type": "credential",
    "credential_id": "..."
  }
}
```

```json
{
  "scope": {
    "type": "repository",
    "repository_id": "..."
  }
}
```

```json
{
  "scope": {
    "type": "repositories",
    "credential_id": "...",
    "repository_ids": ["...", "..."]
  }
}
```

- 成功接收：`202`；返回 `job` 和 `merged`；

```json
{
  "job": {
    "id": "...",
    "trigger": "manual",
    "status": "queued",
    "scope": { "type": "all" },
    "created_at": "2026-07-23T14:30:00Z"
  },
  "merged": false
}
```

如果已有总任务运行，新触发合并到唯一排队任务；接口返回该排队任务且 `merged=true`。不同范围合并后，响应中的 scope 为规范化并集摘要。

范围解释：`all` 只包含已启用凭据下已选中、且主凭据已确定的知识库；`credential` 只包含该凭据作为主凭据的已选知识库；`repository` 使用该知识库当前主凭据；`repositories` 使用指定凭据备份明确列出的一个或多个知识库，且该凭据必须是这些知识库当前可用的主凭据。备用凭据不会因手动任务自动消耗额度。

服务端在创建或合并手动任务前会按 10.2 节重新估算。最近一小时内实际语雀响应头记录的剩余额度小于预计请求数时，拒绝创建；缺少最近额度快照时仍允许排队，由 worker 持续读取真实响应头并处理 429。

- 错误：`404 CREDENTIAL_NOT_FOUND`、`404 REPOSITORY_NOT_FOUND`、`409 NO_ENABLED_TARGETS`、`409 PRIMARY_CREDENTIAL_REQUIRED`、`409 CREDENTIAL_CANNOT_ACCESS_REPOSITORY`、`409 RATE_LIMIT_INSUFFICIENT`、`409 IDEMPOTENCY_CONFLICT`。

### 10.2 备份额度预估

`POST /api/v1/backup-jobs/estimate`

- 权限：管理员会话 + CSRF；
- 请求：与 10.1 节相同的 `scope`，不要求 `Idempotency-Key`；
- 行为：只读取本地知识库、文档计数和凭据额度快照，不在 API 进程中请求语雀；
- 成功：`200`。

```json
{
  "repository_count": 2,
  "document_count": 135,
  "estimated_api_calls": 143,
  "is_precise": false,
  "credentials": [
    {
      "credential_id": "...",
      "credential_name": "个人语雀",
      "repository_count": 2,
      "document_count": 135,
      "estimated_api_calls": 143,
      "rate_limit_limit": 5000,
      "rate_limit_remaining": 4200,
      "rate_limit_observed_at": "2026-07-24T10:00:00Z",
      "snapshot_fresh": true,
      "sufficient": true
    }
  ],
  "calculation_basis": [
    "每个知识库包含详情与目录请求",
    "文档列表按语雀官方每页最多 100 条估算",
    "增量任务包含已删除文档列表请求",
    "按本地已知文档数估算详情请求; 远端变化数和 Table 额外分页无法预先精确获知"
  ]
}
```

`estimated_api_calls` 始终是预计值，`is_precise` 固定为 `false`。估算基于 worker 已确认的调用链、本地已知文档数、语雀文档列表每页最多 100 条和 Table 每页最多 200 行等官方限制；远端新增或变化文档、Table 额外分页、重试和并发产生的共享额度消耗无法在执行前精确获知。

额度上限和剩余量只来自实际响应的 `X-RateLimit-Limit`、`X-RateLimit-Remaining` 快照。快照缺失或观测时间早于当前一小时，`snapshot_fresh=false` 且 `sufficient=null`；这一小时仅用于判断快照是否足够新。当前官方资料没有提供可依赖的 reset 响应头；`next_retry_at` 表示本项目按已确认日额度规则计算的次日重试时间，不宣称它是语雀接口返回的精确重置时间。

- 错误：`404 CREDENTIAL_NOT_FOUND`、`404 REPOSITORY_NOT_FOUND`、`409 NO_ENABLED_TARGETS`、`409 PRIMARY_CREDENTIAL_REQUIRED`、`409 CREDENTIAL_CANNOT_ACCESS_REPOSITORY`。

### 10.3 任务列表

`GET /api/v1/backup-jobs`

- 权限：管理员会话；
- Query：`page`、`page_size`、可选 `status`（可重复）、`trigger`、`credential_id`、`repository_id`、`created_from`、`created_to`；
- 默认排序：`created_at DESC, id DESC`；
- 成功：`200`，分页任务摘要。

任务摘要至少包含：`id`、`trigger`、`scope`、`status`、`progress`、文档/资源计数、`created_at`、`started_at`、`finished_at`、`cancel_requested_at`。`progress` 为 `0` 到 `100` 的百分数，与前端进度条单位一致。

### 10.4 任务详情

`GET /api/v1/backup-jobs/{job_id}`

- 权限：管理员会话；
- 成功：`200`；
- 错误：`404 JOB_NOT_FOUND`。

详情增加：状态原因、等待额度凭据数、下次重试时间、合并来源、问题计数、清理统计和是否可取消/重新执行。

### 10.5 子任务列表

`GET /api/v1/backup-jobs/{job_id}/subtasks`

- 权限：管理员会话；
- Query：`page`、`page_size`、可选 `status`、`credential_id`、`repository_id`；
- 默认排序：`created_at ASC`；
- 成功：`200`，分页子任务；
- 字段包括凭据/知识库安全摘要、状态、计数、`next_retry_at`、最后问题摘要和可空的 `activity`。

`activity` 是从持久化队列实时读取的当前操作快照，不新增另一套任务状态。字段包括：

- `stage`：`queued`、`waiting_retry`、`repository_metadata`、`repository_toc`、`repository_documents`、`repository_deletions`、`document_fetch`、`resource_download`、`resource_retry`、`document_commit`；
- `document_title`、`resource_name`：当前文档和资源的安全显示名称；
- `resource_completed`、`resource_total`：当前文档资源进度；
- `attempt`、`max_attempts`、`retry_in_seconds`、`last_error_code`：当前资源尝试与退避信息；
- `updated_at`：活动快照更新时间。

前端在任务详情打开期间每 4 秒刷新任务、子任务和问题，以展示上述活动；任务状态仍以总任务和子任务的公开状态为准。

### 10.6 问题列表

`GET /api/v1/backup-jobs/{job_id}/issues`

- 权限：管理员会话；
- Query：`page`、`page_size`、可选 `level`、`credential_id`、`repository_id`、`document_id`、`asset_id`、`code`；
- 默认排序：`last_occurred_at DESC, id DESC`；
- 成功：`200`，分页问题。

问题字段：`id`、`level`、`code`、安全 `message`、关联 ID/标题、资源类型、安全 URL、HTTP 状态、尝试次数、首次/最后时间。不得返回 Token、Cookie、请求头、堆栈或带敏感查询参数的 URL。

资源下载错误中，`RESOURCE_TLS_ERROR` 表示资源服务器 HTTPS 证书无法通过可信链校验，属于不可重试错误；不得将其降级为普通 HTTP 或全局关闭证书校验。`RESOURCE_NETWORK_ERROR` 仅用于可能恢复的临时网络异常。

### 10.7 取消任务

`POST /api/v1/backup-jobs/{job_id}/cancel`

- 权限：管理员会话 + CSRF；
- 可取消状态：`queued`、`running`、`waiting_quota`；
- 成功：`202 BackupJob`，写入取消请求；
- 错误：`404 JOB_NOT_FOUND`、`409 JOB_NOT_CANCELLABLE`。

取消在原子单元完成后生效，已提交数据和检查点保留。

### 10.8 重新执行任务

`POST /api/v1/backup-jobs/{job_id}/rerun`

- 权限：管理员会话 + CSRF；
- Header：`Idempotency-Key` 必填；
- 允许来源：`partial`、`failed`、`cancelled`；
- 使用原任务范围创建或合并新的排队任务，并从持久化检查点继续；
- 成功：`202`，结构同创建手动任务；
- 错误：`404 JOB_NOT_FOUND`、`409 JOB_NOT_RERUNNABLE`、`409 IDEMPOTENCY_CONFLICT`。

## 11. 仪表盘

### 11.1 仪表盘摘要

`GET /api/v1/dashboard/summary`

- 权限：管理员会话；
- 成功：`200`。

```json
{
  "schedule": {
    "enabled": true,
    "cron": "0 2 * * *",
    "timezone": "Asia/Shanghai",
    "next_run_at": "2026-07-24T18:00:00Z"
  },
  "current_job": null,
  "last_success_at": "2026-07-23T18:10:00Z",
  "waiting_quota_credentials": 0,
  "job_counts": {
    "succeeded": 10,
    "partial": 1,
    "failed": 0
  },
  "repositories": 8,
  "documents": 1200,
  "versions": 1450,
  "storage": {
    "database_bytes": 10485760,
    "content_bytes": 1073741824,
    "asset_bytes": 734003200,
    "total_bytes": 1084227584
  },
  "worker": {
    "status": "online",
    "last_heartbeat_at": "2026-07-23T14:30:00Z"
  }
}
```

统计来自数据库聚合或缓存，不在每次请求中扫描完整内容目录。

## 12. 设置

### 12.1 获取调度设置

`GET /api/v1/settings/schedule`

- 权限：管理员会话；
- 响应：`200`。

```json
{
  "cron": "0 2 * * *",
  "timezone": "Asia/Shanghai",
  "next_runs": [
    "2026-07-24T18:00:00Z",
    "2026-07-25T18:00:00Z",
    "2026-07-26T18:00:00Z"
  ],
  "updated_at": "2026-07-23T14:30:00Z"
}
```

### 12.2 更新调度设置

`PUT /api/v1/settings/schedule`

- 权限：管理员会话 + CSRF；
- 请求：

```json
{
  "cron": "0 2 * * *",
  "timezone": "Asia/Shanghai"
}
```

- Cron 必须为标准五段；时区必须为有效 IANA 名称；
- 成功：`200`，返回更新后设置和后三次运行时间；
- 错误：`422 INVALID_CRON`、`422 INVALID_TIMEZONE`。

### 12.3 获取保留设置

`GET /api/v1/settings/retention`

- 权限：管理员会话；
- 响应：`200`。

```json
{
  "retention_days": 15,
  "updated_at": "2026-07-23T14:30:00Z"
}
```

### 12.4 更新保留设置

`PUT /api/v1/settings/retention`

- 权限：管理员会话 + CSRF；
- 请求：`{"retention_days": 15}`，必须为正整数；
- 成功：`200`；新值从下一次清理任务生效；
- 错误：`422 VALIDATION_ERROR`。

### 12.5 获取存储设置与用量

`GET /api/v1/settings/storage`

- 权限：管理员会话；
- 响应：`200`。

```json
{
  "database_path": "/data/db",
  "content_path": "/data/content",
  "max_asset_size_bytes": 524288000,
  "max_asset_size_unlimited": false,
  "usage": {
    "database_bytes": 10485760,
    "version_bytes": 340787200,
    "asset_bytes": 734003200,
    "total_bytes": 1085276160
  },
  "updated_at": "2026-07-23T14:30:00Z"
}
```

路径仅展示，不允许通过 API 修改。

### 12.6 更新单资源上限

`PUT /api/v1/settings/storage-limit`

- 权限：管理员会话 + CSRF；
- 有限值：

```json
{
  "max_asset_size_bytes": 524288000
}
```

- 无限制：

```json
{
  "max_asset_size_bytes": null
}
```

- 非空值必须为正整数；
- 成功：`200`，返回存储设置；
- 错误：`422 VALIDATION_ERROR`。

## 13. 删除墓碑

### 13.1 墓碑列表

`GET /api/v1/deletion-tombstones`

- 权限：管理员会话；
- Query：`page`、`page_size`、可选 `q`、`repository_id`、`deleted_from`、`deleted_to`；
- `q` 匹配标题和原路径；
- 默认排序：`deleted_at DESC, id DESC`；
- 成功：`200`，分页墓碑。

墓碑字段：`id`、`base_url`、`yuque_book_id`、`yuque_doc_id`、标题、原路径、知识库摘要、`deleted_at`、`purged_at`、来源任务 ID 和清理任务 ID。

### 13.2 墓碑详情

`GET /api/v1/deletion-tombstones/{tombstone_id}`

- 权限：管理员会话；
- 成功：`200`；
- 错误：`404 TOMBSTONE_NOT_FOUND`。

墓碑没有正文预览或下载接口。

## 14. 权限矩阵

| 接口组 | 未初始化访客 | 未登录访客 | 管理员会话 | CSRF |
| --- | --- | --- | --- | --- |
| `/health/*` | 允许 | 允许 | 允许 | 否 |
| `GET /system/initialization` | 允许 | 允许 | 允许 | 否 |
| `POST /system/initialize` | 允许一次 | 禁止 | 禁止 | 同源校验 |
| `POST /auth/login` | 禁止 | 允许 | 允许重新登录 | 同源校验 |
| 其他 GET/HEAD | 禁止 | 禁止 | 允许 | 否 |
| 其他 POST/PUT/PATCH/DELETE | 禁止 | 禁止 | 允许 | 是 |

MVP 不存在第二种本地角色，不在响应中返回权限数组，也不设计资源级授权字段。

## 15. HTTP 状态码与错误码

### 15.1 HTTP 状态码

| 状态码 | 用途 |
| --- | --- |
| `200` | 查询或同步修改成功 |
| `201` | 初始化或资源创建成功 |
| `202` | 异步操作、任务、取消请求已接收 |
| `204` | 成功且无响应体 |
| `400` | 业务上无效但结构合法的请求 |
| `401` | 未登录、会话失效或登录凭据错误 |
| `403` | CSRF 或同源校验失败 |
| `404` | 本地资源不存在 |
| `409` | 当前状态冲突、重复操作或幂等冲突 |
| `410` | 记录仍在但内容已按保留策略清理 |
| `422` | 请求字段、Cron、时区或 URL 校验失败 |
| `429` | 本地登录限速；语雀 429 不直接作为前端请求响应 |
| `500` | 未预期服务端错误 |
| `503` | 数据库、迁移或内容存储不可用 |

### 15.2 通用错误码

| 错误码 | HTTP | 场景 |
| --- | --- | --- |
| `VALIDATION_ERROR` | 422 | 字段或查询参数不合法 |
| `AUTH_REQUIRED` | 401 | 无有效管理员会话 |
| `INVALID_CREDENTIALS` | 401 | 登录失败，不区分原因 |
| `CSRF_INVALID` | 403 | CSRF 或同源检查失败 |
| `LOGIN_RATE_LIMITED` | 429 | 登录尝试过多 |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | 必需的 Key 缺失 |
| `IDEMPOTENCY_CONFLICT` | 409 | 同 Key 对应不同请求体 |
| `RESOURCE_NOT_FOUND` | 404 | 无更具体错误码时使用 |
| `SERVICE_UNAVAILABLE` | 503 | 数据库或内容目录不可用 |
| `INTERNAL_ERROR` | 500 | 未预期错误；响应不含堆栈 |

### 15.3 业务错误码

| 错误码 | HTTP |
| --- | --- |
| `INITIALIZATION_ALREADY_COMPLETED` | 409 |
| `CURRENT_PASSWORD_INCORRECT` | 400 |
| `CREDENTIAL_NOT_FOUND` | 404 |
| `CREDENTIAL_NAME_EXISTS` | 409 |
| `CREDENTIAL_NOT_VALID` | 409 |
| `INVALID_BASE_URL` | 422 |
| `OPERATION_NOT_FOUND` | 404 |
| `OPERATION_ALREADY_RUNNING` | 409 |
| `REPOSITORY_NOT_FOUND` | 404 |
| `CREDENTIAL_CANNOT_ACCESS_REPOSITORY` | 409 |
| `PRIMARY_CREDENTIAL_REQUIRED` | 409 |
| `DOCUMENT_NOT_FOUND` | 404 |
| `VERSION_NOT_FOUND` | 404 |
| `PREVIEW_NOT_AVAILABLE` | 409 |
| `VERSION_CONTENT_PURGED` | 410 |
| `ASSET_NOT_FOUND` | 404 |
| `ASSET_CONTENT_PURGED` | 410 |
| `JOB_NOT_FOUND` | 404 |
| `JOB_NOT_CANCELLABLE` | 409 |
| `JOB_NOT_RERUNNABLE` | 409 |
| `NO_ENABLED_TARGETS` | 409 |
| `RATE_LIMIT_INSUFFICIENT` | 409 |
| `INVALID_CRON` | 422 |
| `INVALID_TIMEZONE` | 422 |
| `TOMBSTONE_NOT_FOUND` | 404 |

语雀侧网络、429、401/403、文档 404、资源超限和下载失败记录为 Operation 错误或 `BackupIssue`，不伪装成本地 HTTP 错误，也不把语雀原始错误正文直接返回前端。

## 16. 前端调用顺序

### 16.1 首次初始化

1. `GET /system/initialization`；
2. 未初始化时 `POST /system/initialize`；
3. 成功后读取 `GET /auth/me`；
4. 进入凭据设置。

### 16.2 新增凭据与发现知识库

1. `POST /credentials`，取得自动创建的验证 `operation_id`；
2. 轮询 `GET /operations/{id}`；
3. 验证成功后 `POST /credentials/{id}/enable`；
4. `POST /credentials/{id}/discover-repositories`；
5. 轮询操作；
6. 查询 `/repositories`，对重复库调用主凭据接口。

### 16.3 手动备份

1. 查询 `/credentials`，只允许选择已验证并启用的有效凭据；
2. 按凭据查询 `/repositories`，选择该凭据作为可用主凭据且连接正常的一个或多个知识库；
3. 使用 `repositories` 范围调用 `POST /backup-jobs/estimate`，展示备份范围、明确标记为预计的 API 请求数和最近实际额度快照；
4. 额度明确不足时阻止确认；额度充足或未知时，用户确认后调用 `POST /backup-jobs`；
5. 每 3 至 5 秒查询 `GET /backup-jobs/{id}`，需要下钻时查询 subtasks 和 issues；
6. 进入终态后停止轮询并刷新仪表盘、知识库和文档。

### 16.4 定时备份

1. 凭据、知识库选择和额度预估顺序与 16.3 节相同；
2. 在确认步骤增加每日执行时间，沿用 `GET /settings/schedule` 返回的时区；
3. 通过知识库选择接口持久化当前凭据下的 `Repository.selected`，再通过 `PUT /settings/schedule` 保存 `minute hour * * *`；
4. 当前额度不足不阻止保存计划；计划触发后，worker 会将对应子任务标记为 `waiting_quota`、持久化原因和下次尝试时间，额度可用后继续执行。

定时范围继续复用现有全局知识库选择和单一 Cron 设置，不新增独立计划模型。保存当前凭据的选择不会删除其他凭据已保存的知识库选择。

### 16.5 离线浏览

本地浏览、搜索、版本、预览和下载接口只依赖 API、SQLite 和内容目录。语雀不可访问时，这些 GET 接口仍必须正常工作；只有验证、发现和备份任务会受到语雀连接状态影响。

## 17. OpenAPI 与实现要求

- 后端必须为 JSON 接口声明明确的 Pydantic 请求/响应模型和 HTTP 状态码；
- 请求模型默认 `extra=forbid`，关键数字和字符串使用严格类型及边界；
- 时间字段使用带时区 datetime；无时区输入返回 `422`；
- 业务异常统一进入本文错误结构，覆盖 FastAPI 默认 `HTTPException` 和 Pydantic 校验错误格式；
- 下载使用 FastAPI `FileResponse` 或 `StreamingResponse`，必须显式设置媒体类型、安全文件名和 `Content-Disposition`；
- OpenAPI 中必须标注 Cookie 鉴权、CSRF Header、分页模型、枚举和所有已知非 2xx 响应；
- 生产环境的交互式 API 文档默认关闭或仅允许管理员访问；
- 前端生成或手写类型均以服务端 OpenAPI 和本文档为准，不能以页面 Mock 数据为准。

## 18. Context7 官方文档核验

本次通过 Context7 查询并核对以下当前官方资料：

1. [FastAPI Security Reference](https://fastapi.tiangolo.com/reference/security/)：`APIKeyCookie` 可作为依赖统一读取会话 Cookie。
2. [FastAPI Response Reference](https://fastapi.tiangolo.com/reference/response/)：Cookie 支持 `secure`、`httponly` 和 `samesite` 等属性。
3. [FastAPI Status Codes](https://fastapi.tiangolo.com/tutorial/response-status-code/)：路径操作应显式声明成功状态码。
4. [FastAPI Error Handling](https://fastapi.tiangolo.com/tutorial/handling-errors/)：`HTTPException` 支持状态码、详情和自定义 Header；本项目在全局处理器中转换为统一错误模型。
5. [FastAPI Custom Responses](https://fastapi.tiangolo.com/advanced/custom-response/)：`FileResponse`/`StreamingResponse` 用于文件和大内容流式响应，并支持媒体类型、文件名和响应 Header。
6. [Pydantic v2 Strict Mode](https://docs.pydantic.dev/latest/concepts/strict_mode/)：关键字段使用严格校验，避免字符串被静默转换成数字或布尔值。
7. [Pydantic v2 Datetime](https://docs.pydantic.dev/latest/api/standard_library_types/)：使用带时区 datetime 类型并按 ISO 8601 序列化。
8. [Pydantic v2 Serialization](https://docs.pydantic.dev/latest/concepts/serialization/)：PATCH 通过已设置字段和 `exclude_unset` 区分省略字段。
9. [Pydantic v2 Unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions-with-str-discriminators)：任务范围使用 `Literal` 和字段判别器扩展为严格的可辨识联合类型。
10. [Vue Router 4 Nested Routes](https://router.vuejs.org/guide/essentials/nested-routes.html) 与 [Route Meta Fields](https://router.vuejs.org/guide/advanced/meta.html)：备份任务使用子路径和路由元数据实现二级菜单及激活状态。

核验日期：`2026-07-23`。上述资料用于确认框架能力，不改变《需求文档.md》和《技术方案文档.md》的产品边界。
