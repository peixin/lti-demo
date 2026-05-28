# LTI 1.3 Exam Tool (Tool)

[English](README.md)

---

本目录包含基于 Flask + SQLite 构建的 LTI 1.3 考试工具端（Tool/LTI Tool）服务。它负责投递在线测验与考试，解析处理 LTI 1.3 启动的安全载荷，并在学生提交考试后通过 AGS 将成绩回传给平台端 LMS。

### ⚙️ 容器化改动简述
本服务已被完全重构为一个独立的 Docker 项目：
- **环境变量控制**：所有运行配置均由本地 `.env` 文件进行控制（包含端口、密钥、管理员密码等）。
- **数据持久化映射**：SQLite 数据库统一持久化挂载到本地 of `./data` 目录中（容器内对应路径 `/app/data/exam-tool.db`）。
- **一键快捷构建**：提供可执行构建脚本 `./build.sh`，可快速编译本地容器镜像。

### 🚀 配置与启动步骤

**1. 配置运行环境变量**
复制 `.env.example` 并重命名为 `.env`：
```bash
cp .env.example .env
```
*您可以在 `.env` 中随意更改外部暴露端口 `PORT`（默认 8002）、修改 `SECRET_KEY` 以及自定义后台管理密码 `ADMIN_PASSWORD`。*

**2. 构建 Docker 镜像**
直接执行一键构建脚本（该脚本会将镜像打标签为 `beiming/lti-exam-tool:latest`）：
```bash
./build.sh
```
*（或者您也可以使用 `docker compose build` 进行构建）*

**3. 启动容器服务**
在后台运行并拉起容器：
```bash
docker compose up -d
```

**4. 浏览器访问**
打开浏览器访问考试工具界面或管理后台：
* 用户/学生考试入口：`http://localhost:8002`（常规测验需由 Platform 端发起 LTI 启动流方能做题）。
* 后台管理控制台：`http://localhost:8002/admin`（默认登录密码：`admin`，或您在 `.env` 中修改的值）。
