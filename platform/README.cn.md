# LTI 1.3 Platform (LMS)

[English](README.md)

---

本目录包含基于 Flask + SQLite 构建的 LTI 1.3 平台端（Platform/LMS）服务。它扮演学习管理系统（LMS）的角色，用于管理用户、课程、活动，并向 LTI 1.3 工具提供注册对接服务。

### ⚙️ 容器化改动简述
本服务已被完全重构为一个独立的 Docker 项目：
- **环境变量控制**：所有运行配置均由本地 `.env` 文件进行控制（包含端口、密钥等）。
- **数据持久化映射**：SQLite 数据库统一持久化挂载到本地的 `./data` 目录中（容器内对应路径 `/app/data/platform.db`）。
- **一键快捷构建**：提供可执行构建脚本 `./build.sh`，可快速编译本地容器镜像。

### 🚀 配置与启动步骤

**1. 配置运行环境变量**
复制 `.env.example` 并重命名为 `.env`：
```bash
cp .env.example .env
```
*您可以在 `.env` 中随意更改外部暴露端口 `PORT`（默认 8001）或修改 `SECRET_KEY`。*

**2. 构建 Docker 镜像**
直接执行一键构建脚本（该脚本会将镜像打标签为 `beiming/lti-platform:latest`）：
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
打开浏览器访问平台端控制台：
* 访问地址：`http://localhost:8001`（或您在 `.env` 中自定义的端口）。
