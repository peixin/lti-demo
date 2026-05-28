# LTI 1.3 Demo

[English](README.md)

---

本仓库是一个关于 **LTI 1.3** (Learning Tools Interoperability) 标准的极简端到端完整实现 Demo。它由两个完全独立的 Flask + SQLite 服务构成，支持完整的 OIDC 登录启动流以及通过 AGS 进行考试成绩的回传。

### ✨ 核心亮点 & 宣传特色
- **100% 原生参考代码**：由纯粹的 Python Flask 与原生 SQLite SQL 语句编写，零 ORM 重型依赖，是深入透视和学习 LTI 1.3 底层安全握手机制的黄金参考沙箱。
- **企业级安全对接沙箱**：包含真实的非对称 RSA-2048 秘钥对自动生成（JWKS）、真实的 RS256 Token 签名校验（PyJWT）、OIDC 表单 POST 启动，以及完整规范的 JWT Claims 校验。
- **全方位多语言（i18n）**：开箱即用支持英文、简体中文、繁体中文和韩文，具备浏览器首选语言自动侦测与一键手动切换功能。
- **高颜值 Bulma 界面**：基于 Flask + Jinja2 模板，采用 Bulma CSS 进行极简且高级感的微交互和微动画设计，没有任何复杂的前端打包编译流程，开箱即用。

### 🛡️ 支持的 LTI 1.3 核心协议规范
本项目全面实现了 LTI 1.3 技术协议矩阵：
- **OIDC 核心登录启动 (LtiResourceLinkRequest)**：支持安全的单点登录认证，完美引导学生进入并进行在线测验。
- **深层链接 (LtiDeepLinkingRequest / Response)**：支持教师在平台端快速唤起工具端内容选择器，挑选不同的考试题库分类并双向安全绑定。
- **成绩与作业服务 (AGS 2.0)**：支持积分辅助项（Lineitems）创建同步、实时成绩回传（带幂等保护及 submissionId 传递）以及拉取成绩单。
- **班级花名册与角色服务 (NRPS)**：支持从 LMS 平台端实时、动态同步整个班级的师生花名册及角色状态。
- **答卷回顾服务 (LtiSubmissionReviewRequest)**：支持教师在 LMS 成绩簿中，一键安全单点跳转至测验工具端只读调阅特定学生的考试作答明细。

### 🧬 自定义扩展字段与 Claims
我们在真实场景的对接中对 LTI 字段进行了高级扩展：
- **`custom.category`**：实现基于 LTI 绑定参数的在线考试分类精准路由。
- **`custom.tool_event_id` / `submissionId`**：基于单次考试答卷 UUID 的防重复回传控制，结合 AGS 成绩包实现三级降级保护的答卷详情追踪调阅。
- **`answering_duration`（答卷耗时）**：精准捕捉并存储学生作答试卷所消耗的秒数。

### 📂 独立解耦的 Docker 容器化
平台端（Platform）和考试端（Exam Tool）已经被完全解耦并重构为**两个完全独立的项目**（均运行在 Python 3.12 容器内并独立使用 SQLite 数据库）：
- **独立的环境控制**：分别由各自目录下的 `.env` 文件进行灵活配置。
- **稳健的数据持久化**：数据库统一挂载到宿主机各自的 `./data` 文件夹（分别包含 `platform.db` 和 `exam-tool.db`），有效规避 SQLite 的锁与 inode 变更问题。
- **一键快捷构建**：各目录下提供了 `build.sh` 脚本，可快速在本地进行镜像编译并打标签。

---

### 🚀 快速启动指南
如需获取这两个独立服务的具体配置与启动细节，请参阅它们各自目录下的说明文档：

* 🏫 **平台端服务（LMS Platform）**：前往 [platform/README.cn.md](platform/README.cn.md) 了解如何管理用户、课程和注册对接 LTI 工具。
* 📝 **考试工具端服务（Exam Tool）**：前往 [exam-tool/README.cn.md](exam-tool/README.cn.md) 了解如何运送在线考试和处理 LTI 启动载荷。
