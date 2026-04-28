# LTI 1.3 Demo

A minimal, self-contained demo of the [LTI 1.3](https://www.imsglobal.org/spec/lti/v1p3/) (Learning Tools Interoperability) standard — two independent services that talk to each other over the full OIDC launch flow with grade passback via AGS.

LTI 1.3 标准的最小化完整实现 Demo —— 两个独立服务通过完整 OIDC 启动流程互通，并支持 AGS 成绩回传。

---

## What's inside / 项目结构

| Service | Port | Role |
|---|---|---|
| `platform/` | 8001 | LMS — manages users, courses, activities, grades |
| `exam-tool/` | 8002 | LTI Tool — delivers online exams, reports scores |

Both services are plain Flask + SQLite, no ORM, no frontend build step.  
两个服务均为 Flask + SQLite，无 ORM，无前端构建步骤。

---

## Docs / 文档

- [Technical Reference](docs/technical.md) — architecture, DB schema, routes, LTI 1.3 flow diagrams
- [LTI Guide](docs/lti-guide.md) — LTI concepts from scratch, OIDC flow, JWT, AGS explained in depth

---

## Requirements / 环境要求

- Python 3.12+
- [Poetry](https://python-poetry.org/)
- [just](https://github.com/casey/just)

---

## Setup / 启动步骤

**1. Install dependencies / 安装依赖**

```bash
just install
```

**2. Start both services / 启动两个服务**

```bash
just dev
```

Or start them separately / 或分别启动：

```bash
just platform   # http://localhost:8001
just exam-tool  # http://localhost:8002
```

---

## First-time configuration / 首次配置

LTI requires a one-time registration handshake between Platform and Tool.  
LTI 需要在 Platform 和 Tool 之间完成一次注册配置。

**Step 1 — Register the Tool on the Platform / 在 Platform 注册 Tool**

1. Open [http://localhost:8001/tools/add](http://localhost:8001/tools/add)
2. Fill in the Tool's endpoints (copy from exam-tool admin page):
   - Login URL: `http://localhost:8002/lti/login`
   - Redirect URI: `http://localhost:8002/lti/launch`
   - JWKS URL: `http://localhost:8002/lti/jwks`
   - Target Link URI: `http://localhost:8002/exam`
3. Submit — Platform auto-generates a `client_id` and `deployment_id`

**Step 2 — Configure the Tool with Platform credentials / 在 Tool 填入 Platform 配置**

1. Open [http://localhost:8002/admin](http://localhost:8002/admin) (default password: `admin`)
2. Copy the Platform OIDC endpoints from [http://localhost:8001/tools](http://localhost:8001/tools)
3. Fill in and save: ISS, Client ID, Deployment ID, OIDC Auth URL, JWKS URL, Token URL

**Step 3 — Launch / 启动考试**

1. Log in at [http://localhost:8001](http://localhost:8001) (register a user first)
2. Create a course, add an activity linked to the exam tool
3. Click the activity — the LTI 1.3 launch flow runs automatically
4. Complete the exam — score is passed back to the Platform via AGS

---

## Tech stack / 技术栈

- **Backend**: Python, Flask, SQLite (raw SQL)
- **Frontend**: Jinja2 templates, [Bulma CSS](https://bulma.io/) via CDN
- **LTI auth**: PyJWT + cryptography (RS256, RSA-2048)
- **Standard**: [LTI 1.3](https://www.imsglobal.org/spec/lti/v1p3/) + [AGS](https://www.imsglobal.org/spec/lti-ags/v2p0/)
