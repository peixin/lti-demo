# LTI Demo 技术文档

## 项目概览

本项目是一个 LTI 1.3 协议的 Demo 实现，包含两个独立服务：

| 服务 | 目录 | 端口 | 角色 |
|---|---|---|---|
| Platform | `platform/` | 8001 | LMS，管理课程、工具、成绩 |
| Exam Tool | `exam-tool/` | 8002 | LTI 工具，提供在线考试 |

两者通过 LTI 1.3 协议通信，不共享数据库。

---

## 技术选型

### 后端

**Flask 3.x**（Python）：轻量路由+模板一体，无额外配置，适合 Demo 规模。所有逻辑集中在单个 `app.py`。

**SQLite 3**：零配置，文件即数据库，Python 内置 `sqlite3` 模块无需安装驱动。不使用 ORM，直接写 SQL，表结构内联在 `SCHEMA` 字符串里，启动时 `CREATE TABLE IF NOT EXISTS` 自动建表。

### 前端

**Jinja2 模板**（Flask 内置）：服务端渲染 HTML，无前后端分离，无构建步骤。

**Bulma 0.9.4**（CDN 引入）：纯 CSS，无 JavaScript 依赖。

### 依赖库

```
flask>=3.0          # web 框架
requests>=2.31      # exam-tool 向 platform 发起 AGS HTTP 请求
pyjwt[crypto]>=2.8  # JWT 编解码 + RSA 签名（含 cryptography）
```

LTI 1.3 / OIDC 核心功能用 `PyJWT` + `cryptography` 实现，无第三方 LTI 库。

### 工程

**Poetry**（根目录 `pyproject.toml`）：两个服务共享同一个虚拟环境。

**just**（`justfile`）：定义启动命令。

---

## 目录结构

```
lti-demo/
├── pyproject.toml
├── justfile
├── platform/
│   ├── app.py              # Flask 应用，port 8001
│   ├── lti.py              # LTI 1.3 工具函数（platform 侧）
│   ├── platform.db         # SQLite（运行时生成）
│   └── templates/
│       ├── base.html
│       ├── login.html / register.html
│       ├── dashboard.html
│       ├── course_detail.html
│       ├── tools.html / add_tool.html
│       └── oidc_response.html   # OIDC Step 3 自动提交表单
└── exam-tool/
    ├── app.py              # Flask 应用，port 8002
    ├── lti.py              # LTI 1.3 工具函数（tool 侧）
    ├── exam-tool.db
    └── templates/
        ├── base.html
        ├── admin_login.html / admin.html
        ├── exam.html / result.html
        └── error.html
```

---

## Platform 详解

### 数据库表设计

#### `users` — 平台用户

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键，也作为 LTI 的 `sub`（用户标识）传入 JWT |
| `username` | TEXT UNIQUE | 用户名 |
| `password_hash` | TEXT | SHA-256 哈希 |

#### `platform_config` — 平台 RSA 密钥

单行表（`id=1`），首次启动时自动生成。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 固定为 1 |
| `kid` | TEXT | Key ID，用于在 JWKS 中标识密钥 |
| `private_key_pem` | TEXT | RSA-2048 私钥（PEM），用于签名 id_token |
| `public_key_pem` | TEXT | 对应公钥（PEM），暴露到 `/lti/jwks` 供 tool 验证 |

#### `lti_tools` — 已注册的 LTI 1.3 工具

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `name` | TEXT | 工具显示名称 |
| `client_id` | TEXT UNIQUE | 平台颁发给工具的标识符，格式 `client_xxxx`，注册时随机生成 |
| `deployment_id` | TEXT UNIQUE | 部署标识符，格式 `dep_xxxx`，注册时随机生成。一个工具可在不同部署中使用 |
| `login_url` | TEXT | 工具的 OIDC 登录发起端点，平台在 Launch 时跳转到这里 |
| `redirect_uri` | TEXT | 工具的 OIDC 回调端点，平台将 id_token POST 到这里 |
| `jwks_url` | TEXT | 工具的公钥端点，平台从这里取公钥以验证工具的 JWT 断言 |
| `target_link_uri` | TEXT | 工具的资源默认地址，写入 JWT 告知工具要展示什么 |

#### `courses` — 课程

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `name` | TEXT | 课程名称 |
| `teacher_id` | INTEGER | 创建者，关联 `users.id` |

#### `activities` — 课程活动（工具挂载实例）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `course_id` | INTEGER | 所属课程 |
| `tool_id` | INTEGER | 使用的工具，关联 `lti_tools.id` |
| `name` | TEXT | 活动名称 |
| `resource_link_id` | TEXT UNIQUE | 格式 `rl_<uuid>`，标识这个工具在课程中的唯一挂载点，写入 JWT 的 `resource_link.id` |

#### `lineitems` — AGS 成绩项

每个 activity 对应一条 lineitem，第一次 launch 时自动创建。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键，也是 AGS 端点路径中的 ID |
| `activity_id` | INTEGER UNIQUE | 关联 `activities.id` |
| `label` | TEXT | 成绩项名称，取自活动名称 |

#### `grades` — 成绩本

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `user_id` | INTEGER | 学生用户 id，与 JWT `sub` 对应 |
| `activity_id` | INTEGER | 对应活动 |
| `course_id` | INTEGER | 冗余字段，方便按课程查询 |
| `score` | REAL | 0.0～1.0，NULL 表示未完成，AGS 回传后更新 |
| `updated_at` | DATETIME | 最后更新时间 |
| UNIQUE | `(user_id, activity_id)` | 同一学生同一活动只有一条成绩 |

#### `access_tokens` — AGS Bearer Token

| 字段 | 类型 | 说明 |
|---|---|---|
| `token` | TEXT PK | 随机 hex 字符串 |
| `client_id` | TEXT | 颁发给哪个工具 |
| `expires_at` | INTEGER | Unix 时间戳，1 小时有效期 |

### 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/login` | 登录 |
| GET/POST | `/register` | 注册 |
| GET | `/logout` | 退出 |
| GET | `/` | Dashboard |
| GET | `/tools` | 已注册工具列表，含平台 OIDC 端点信息 |
| GET/POST | `/tools/add` | 注册新工具 |
| POST | `/courses/add` | 创建课程 |
| GET | `/courses/<id>` | 课程详情 + 成绩本 |
| POST | `/courses/<id>/activities/add` | 添加活动 |
| GET | `/lti/launch/<activity_id>` | LTI Step 1：重定向到工具 login URL |
| GET | `/lti/oidc/auth` | LTI Step 3：OIDC 授权端点，签发 id_token |
| GET | `/lti/jwks` | 平台公钥 JWKS |
| POST | `/lti/token` | OAuth2 Token 端点（JWT Bearer → access token）|
| POST | `/lti/ags/lineitems/<id>/scores` | AGS 成绩接收 |
| GET | `/.well-known/openid-configuration` | OIDC Discovery |

### Session 机制

Cookie 名 `platform_session`，`session.permanent=True`，有效期 7 天。

---

## Exam Tool 详解

### 数据库表设计

#### `tool_config` — 工具 RSA 密钥 + 平台 OIDC 配置

单行表（`id=1`），密钥首次启动时自动生成，平台 OIDC 配置由管理员在 `/admin` 填写。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 固定为 1 |
| `kid` | TEXT | Key ID |
| `private_key_pem` | TEXT | RSA-2048 私钥，用于签名 JWT Bearer 断言（AGS 鉴权）|
| `public_key_pem` | TEXT | 对应公钥，暴露到 `/lti/jwks` 供平台验证断言 |
| `platform_iss` | TEXT | 平台 Issuer URL，如 `http://localhost:8001` |
| `client_id` | TEXT | 平台颁发的 client_id |
| `deployment_id` | TEXT | 平台颁发的 deployment_id |
| `platform_oidc_auth_url` | TEXT | 平台 OIDC 授权端点 |
| `platform_jwks_url` | TEXT | 平台公钥端点，用于验证 id_token 签名 |
| `platform_token_url` | TEXT | 平台 Token 端点，用于申请 AGS access token |

#### `oidc_state` — OIDC 流程临时状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `state` | TEXT PK | 随机字符串，防 CSRF，工具在 Step 2 生成，Step 4 验证 |
| `nonce` | TEXT | 随机字符串，防重放，写入 auth 请求，平台原样放入 JWT，工具验证是否匹配 |
| `created_at` | DATETIME | 超过 10 分钟自动清理 |

#### `lti_sessions` — LTI 会话

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `session_id` | TEXT UNIQUE | UUID，存入浏览器 Cookie，后续请求用于找回上下文 |
| `sub` | TEXT | 来自 JWT `sub`，即平台侧的用户 ID，AGS 回传成绩时用作 `userId` |
| `user_name` | TEXT | 来自 JWT `name` |
| `deployment_id` | TEXT | 来自 JWT，验证后存储 |
| `resource_link_id` | TEXT | 来自 JWT，标识平台侧的活动挂载点 |
| `context_id` | TEXT | 来自 JWT，平台侧的课程 ID |
| `lineitem_url` | TEXT | 来自 JWT AGS claim，提交成绩的目标 URL |
| `return_url` | TEXT | 来自 JWT launch_presentation claim，考完跳回平台的地址 |
| `created_at` | DATETIME | 创建时间 |

#### `questions` — 题库

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `text` | TEXT | 题目文本 |
| `options` | TEXT | 选项 JSON 数组，如 `["A", "B", "C", "D"]` |
| `answer` | INTEGER | 正确答案选项索引（0-based） |

#### `attempts` — 答题记录

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `session_id` | TEXT | 关联 `lti_sessions.session_id` |
| `answers` | TEXT | 用户答案 JSON，格式 `{"question_id": chosen_index}`，`-1` 表示未作答 |
| `score` | REAL | 0.0～1.0，正确数 / 总题数 |
| `submitted_at` | DATETIME | 提交时间 |

### 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/admin/login` | Admin 登录（密码由 `ADMIN_PASSWORD` 环境变量配置，默认 `admin`）|
| GET | `/admin/logout` | Admin 退出 |
| GET/POST | `/admin` | Admin 面板：平台配置、答题记录、题目 |
| GET | `/lti/jwks` | 工具公钥 JWKS |
| GET/POST | `/lti/login` | LTI Step 2：生成 state/nonce，重定向到平台 OIDC 授权端点 |
| POST | `/lti/launch` | LTI Step 4：验证 id_token，建立会话，跳转考试页 |
| GET | `/exam` | 考试页（需要有效 LTI 会话）|
| POST | `/exam/submit` | 提交答案，计算分数，通过 AGS 回传成绩 |
| GET | `/result` | 结果页，含"Return to Platform"按钮 |

---

## LTI 1.3 核心实现

### `platform/lti.py`

| 函数 | 说明 |
|---|---|
| `generate_key_pair()` | 生成 RSA-2048 密钥对，返回 (private_pem, public_pem, kid) |
| `public_key_to_jwk(public_pem, kid)` | 将 PEM 公钥转为 JWK dict，用于 `/lti/jwks` 响应 |
| `make_id_token(...)` | 构建并用私钥 RS256 签名 LTI 1.3 id_token JWT |
| `verify_tool_jwt(token, jwks_url, token_url)` | 在 Token 端点验证工具发来的 JWT Bearer 断言 |

### `exam-tool/lti.py`

| 函数 | 说明 |
|---|---|
| `generate_key_pair()` | 同上 |
| `public_key_to_jwk(public_pem, kid)` | 同上 |
| `verify_id_token(token, jwks_url, client_id, iss)` | 从平台 JWKS 取公钥，验证 id_token 签名、iss、aud、exp |
| `get_access_token(token_url, private_pem, kid, client_id)` | 构造 JWT Bearer 断言，向平台 Token 端点换取 AGS access token |
| `post_score(access_token, lineitem_url, sub, score)` | 向 AGS lineitem 提交成绩（JSON，Bearer 鉴权）|

---

## 完整流程图

### LTI 1.3 四步 Launch 流程

```
用户浏览器              Platform (8001)            Exam Tool (8002)
    │                       │                            │
    │  GET /lti/launch/<id>  │                            │
    │──────────────────────>│                            │
    │                       │  生成 login 参数            │
    │  302 → tool login URL  │  (iss, login_hint,         │
    │       ?iss=&login_hint │   lti_message_hint,        │
    │       &lti_message_hint│   client_id)               │
    │<──────────────────────│                            │
    │                        Step 1                       │
    │  GET /lti/login        │                            │
    │──────────────────────────────────────────────────>│
    │                       │             生成 state, nonce
    │                       │             存入 oidc_state 表
    │  302 → platform OIDC auth                          │
    │       ?response_type=id_token                      │
    │       &state=<state>                               │
    │       &nonce=<nonce>                               │
    │<──────────────────────────────────────────────────│
    │                        Step 2                       │
    │  GET /lti/oidc/auth    │                            │
    │──────────────────────>│                            │
    │                       │  验证 client_id, redirect_uri
    │                       │  创建 lineitem (首次)       │
    │                       │  RS256 签名 id_token JWT   │
    │  渲染 oidc_response.html                           │
    │  (含隐藏表单，JS 自动 submit)                       │
    │<──────────────────────│                            │
    │                        Step 3                       │
    │  POST /lti/launch      │                            │
    │  id_token=<JWT>        │                            │
    │  state=<state>         │                            │
    │──────────────────────────────────────────────────>│
    │                       │           验证 state（防CSRF）
    │                       │           从平台 JWKS 取公钥
    │                       │           验证 JWT 签名/nonce
    │                       │           验证 deployment_id
    │                       │           建立 lti_session  │
    │  302 → /exam           │                            │
    │<──────────────────────────────────────────────────│
    │                        Step 4                       │
    │  GET /exam             │                            │
    │──────────────────────────────────────────────────>│
    │  渲染考试页面                                        │
    │<──────────────────────────────────────────────────│
```

### AGS 成绩回传流程

```
Exam Tool (8002)                          Platform (8001)
    │                                           │
    │  POST /lti/token                          │
    │  grant_type=client_credentials            │
    │  client_assertion=<tool签名的JWT>          │
    │──────────────────────────────────────────>│
    │                                           │  从工具 JWKS 取公钥
    │                                           │  验证 JWT 断言
    │  { access_token: "xxx" }                  │
    │<──────────────────────────────────────────│
    │                                           │
    │  POST /lti/ags/lineitems/<id>/scores      │
    │  Authorization: Bearer xxx                │
    │  Content-Type: application/vnd.ims...     │
    │  { userId, scoreGiven, scoreMaximum, ... }│
    │──────────────────────────────────────────>│
    │                                           │  UPDATE grades SET score=?
    │  204 No Content                           │
    │<──────────────────────────────────────────│
```

---

## 初始配置流程（一次性）

```
1. 启动两个服务
   just platform   # port 8001
   just exam-tool  # port 8002

2. localhost:8002/admin → 查看 "Tool Endpoints"（4个URL）

3. localhost:8001 → 登录 → Tools → Register Tool
   填入 exam-tool 的 4 个端点 URL
   注册后显示生成的 client_id, deployment_id 和平台 OIDC 端点

4. localhost:8002/admin → 填入平台配置（6个字段）→ Save

5. 之后：创建课程 → 添加活动 → Launch 即可
```

---

## Demo 局限性说明

1. **密码存储**：SHA-256，生产环境应改用 `argon2` 或 `bcrypt`。
2. **RSA 密钥持久化**：存在 SQLite 里，未加密。生产环境应使用 KMS 或加密存储。
3. **Nonce 防重放**：`oidc_state` 表验证后立即删除，但未校验时间戳窗口。
4. **Cookie 隔离**：两个服务的 session cookie 名分别为 `platform_session` 和 `examtool_session`，因为浏览器 cookie 按域名（不按端口）存储，同名会互相覆盖。
5. **AGS token 存储**：access token 存在 SQLite 里，未做 Redis 缓存。
