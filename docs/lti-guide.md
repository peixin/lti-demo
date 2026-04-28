# LTI 入门指南

## 什么是 LTI

LTI（Learning Tools Interoperability，学习工具互操作性）是 1EdTech（原 IMS Global）制定的标准，解决一个问题：**如何让不同厂商的教育软件无缝集成在一起？**

没有 LTI 之前，学校想在自己的 LMS（学习管理系统，如 Moodle、Canvas）里嵌入第三方工具（如在线实验室、考试系统、视频平台），需要各自定制开发，互不兼容。LTI 规定了统一的"插槽"规格，任何遵守这个规格的工具都能插入任何遵守这个规格的 LMS，如同 USB 接口标准化了所有外设。

---

## 两个角色

LTI 里只有两个参与方：

**Platform（平台，即 LMS）**

管理用户、课程、成绩的一方。Canvas、Moodle、Blackboard 都是 Platform。Platform 是"甲方"，决定什么人可以用什么工具，收集并展示成绩。

**Tool（工具，即 Tool Provider）**

提供某种具体功能的一方，如考试系统、视频播放、代码编辑器。Tool 不管理用户，它的用户身份由 Platform 在每次启动时传入。Tool 是"乙方"，只管干活。

这个关系是**单向的、不对等的**：Platform 发起，Tool 响应，Tool 不能主动找 Platform 要信息，只能在 Platform 找它的时候干活并汇报结果（成绩）。

---

## LTI 版本

目前有两个主要版本，差异很大：

### LTI 1.1（历史版本）

- 认证方式：OAuth 1.0a（HMAC-SHA1 签名）
- 成绩回传：Basic Outcomes Service（发 XML）
- 配置简单：一对 key+secret 就能跑
- 现状：老系统广泛使用，新项目不推荐

### LTI 1.3（当前推荐）

- 认证方式：OpenID Connect（OIDC）+ JWT（JSON Web Token）
- 成绩回传：Assignment and Grade Services（AGS，REST API）
- 更安全：非对称加密，Token 带有效期
- 现状：新项目标准，**本项目实现的就是这个版本**

简单类比：LTI 1.1 像发短信验证码，LTI 1.3 像用指纹+密钥的双因素认证。

---

## LTI 1.1 核心概念（背景参考）

> 本节说明 LTI 1.1 的工作方式，帮助理解 LTI 1.3 改进了什么。本项目不使用这套机制。

### 信任建立：Consumer Key + Secret

Platform 和 Tool 线下交换一对对称凭证：

```
Consumer Key:    key_abc123def456    （可以公开）
Consumer Secret: f8e9d2c1b0a4...     （必须保密）
```

### LTI 1.1 启动流程

Platform 把用户信息打包，用 HMAC-SHA1 签名后，通过浏览器表单 POST 给 Tool：

```html
<form method="post" action="http://tool.example.com/lti/launch">
  <input type="hidden" name="lti_message_type" value="basic-lti-launch-request">
  <input type="hidden" name="user_id" value="42">
  <input type="hidden" name="lis_person_name_full" value="张三">
  <input type="hidden" name="oauth_signature" value="YWJj...">
</form>
<script>document.forms[0].submit();</script>
```

Tool 收到后重新计算签名，一致则信任，建立 Session，展示内容。

### 成绩回传：Basic Outcomes XML

Tool 向平台发一段 XML，平台解析后更新成绩：

```xml
<replaceResultRequest>
  <sourcedGUID><sourcedId>1::42::nonce</sourcedId></sourcedGUID>
  <result><resultScore><textString>0.8000</textString></resultScore></result>
</replaceResultRequest>
```

**LTI 1.1 的缺陷**：共享 secret 一旦泄漏，攻击者可完全伪造请求；XML 格式笨重；无法支持复杂成绩结构。

---

## LTI 1.3 核心机制详解

### 一、信任建立：公私钥对 + 提前注册

LTI 1.3 抛弃了共享 secret，改用非对称加密：

- Platform 生成一对 RSA 密钥，私钥签名，公钥公开（通过 `/lti/jwks` 端点）
- Tool 也生成一对 RSA 密钥，用于在请求 Token 时向平台证明身份
- 双方互相知道对方的 JWKS URL，运行时动态获取公钥验证签名

注册时需要交换的信息：

| Platform 给 Tool | Tool 给 Platform |
|---|---|
| Issuer (ISS) | Login URL |
| Client ID | Redirect URI |
| Deployment ID | JWKS URL |
| OIDC Auth URL | Target Link URI |
| JWKS URL | — |
| Token URL | — |

私钥**从不离开**各自的服务器，这是 LTI 1.3 安全性的根本。

### 二、LTI 1.3 四步启动流程（OIDC）

LTI 1.3 的 Launch 是一个完整的 OIDC 认证流程：

```
Step 1  浏览器 → Platform
        用户点击活动，Platform 把浏览器重定向到 Tool 的 Login URL
        携带：iss, login_hint, target_link_uri, lti_message_hint

Step 2  浏览器 → Tool (Login URL)
        Tool 生成随机 state + nonce，存入数据库
        Tool 把浏览器重定向到 Platform 的 OIDC Auth URL
        携带：client_id, redirect_uri, state, nonce, scope=openid

Step 3  浏览器 → Platform (OIDC Auth URL)
        Platform 验证 client_id，找到对应活动
        用自己的 RSA 私钥签名，生成 id_token（JWT）
        JWT 里包含用户信息、课程信息、AGS 端点等
        Platform 渲染一个自动提交的表单，把 id_token + state POST 给 Tool

Step 4  浏览器 → Tool (Redirect URI / Launch URL)
        Tool 收到 id_token + state
        验证 state 匹配（防 CSRF）
        从 Platform 的 JWKS URL 取公钥，验证 JWT 签名、iss、aud、exp
        建立 LTI Session，把用户定向到考试页面
```

关键点：**所有用户信息都在 JWT 里**，JWT 被 Platform 的私钥签名，任何人都可以用公钥验证，但无法伪造。

### 三、id_token（JWT）的内容

Platform 签发的 id_token 是一个 JWT，解码后包含：

**标准 OIDC Claims**

| 字段 | 含义 |
|---|---|
| `iss` | Platform 的 Issuer URL，证明来源 |
| `sub` | 用户在 Platform 的唯一 ID |
| `aud` | Tool 的 client_id，确认接收方 |
| `iat` / `exp` | 签发时间 / 过期时间（防重放） |
| `nonce` | Tool 在 Step 2 生成的随机数，Tool 在 Step 4 验证它匹配 |

**LTI 扩展 Claims**

| 字段 | 含义 |
|---|---|
| `https://purl.imsglobal.org/spec/lti/claim/message_type` | `LtiResourceLinkRequest` |
| `https://purl.imsglobal.org/spec/lti/claim/version` | `1.3.0` |
| `https://purl.imsglobal.org/spec/lti/claim/deployment_id` | Deployment ID |
| `https://purl.imsglobal.org/spec/lti/claim/resource_link` | 包含 `id`、`title` 的对象 |
| `https://purl.imsglobal.org/spec/lti/claim/context` | 课程 ID 和名称 |
| `https://purl.imsglobal.org/spec/lti/claim/lis` | 包含 `person_name_full` 的对象 |
| `https://purl.imsglobal.org/spec/lti/claim/roles` | 角色数组，如 `["Learner"]` |

id_token e.g.
```json
{
  "iss": "http://127.0.0.1:8001",
  "sub": "1",
  "aud": "client_7626fdf0d880",
  "iat": 1777363198,
  "exp": 1777363498,
  "nonce": "b09b22df31164ff99bbb0068814d2245",
  "name": "liupeixin",
  "given_name": "liupeixin",
  "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiResourceLinkRequest",
  "https://purl.imsglobal.org/spec/lti/claim/version": "1.3.0",
  "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "dep_20b9b17a3b76",
  "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": "http://localhost:8002/exam",
  "https://purl.imsglobal.org/spec/lti/claim/resource_link": {
    "id": "rl_d6d9e6c972bd4f6dbc21fec64d02477d",
    "title": "Exam 1"
  },
  "https://purl.imsglobal.org/spec/lti/claim/roles": [
    "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"
  ],
  "https://purl.imsglobal.org/spec/lti/claim/context": {
    "id": "1",
    "type": [
      "http://purl.imsglobal.org/vocab/lis/v2/course#CourseOffering"
    ]
  },
  "https://purl.imsglobal.org/spec/lti-ags/claim/endpoint": {
    "scope": [
      "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
      "https://purl.imsglobal.org/spec/lti-ags/scope/score"
    ],
    "lineitems": "http://127.0.0.1:8001/lti/ags/lineitems/1",
    "lineitem": "http://127.0.0.1:8001/lti/ags/lineitems/1"
  },
  "https://purl.imsglobal.org/spec/lti/claim/launch_presentation": {
    "return_url": "http://127.0.0.1:8001/courses/1"
  }
}
```


**AGS（成绩服务）Claims**

| 字段 | 含义 |
|---|---|
| `https://purl.imsglobal.org/spec/lti-ags/claim/endpoint` | 包含 `lineitem`（成绩单条目 URL）和 `scope`（可用权限） |

Tool 从这个 claim 里取出 `lineitem` URL，后续用于回传成绩。

### 四、JWKS 与签名验证

每个服务都暴露一个 `/lti/jwks` 端点，返回自己的公钥集合：

```json
{
  "keys": [{
    "kty": "RSA",
    "use": "sig",
    "alg": "RS256",
    "kid": "key-uuid-xxx",
    "n": "...",
    "e": "AQAB"
  }]
}
```

`kid`（Key ID）字段用于匹配：JWT Header 里包含 `"kid": "key-uuid-xxx"`，验证方从 JWKS 里找到同 kid 的公钥，用它验证签名。这样即使密钥轮换，也能用 kid 找到正确的公钥。

### 五、AGS 成绩回传（Assignment and Grade Services）

LTI 1.3 的成绩回传是标准 REST API，分两步：

**Step A：获取 Access Token**

Tool 向 Platform 的 Token URL 发 POST，使用 JWT Bearer 授权方式（OAuth2 `client_credentials` 流程）：

```
POST /lti/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
&client_assertion=<Tool 用自己私钥签名的 JWT>
&scope=https://purl.imsglobal.org/spec/lti-ags/scope/score
```

Tool 签名的 JWT（client_assertion）内容：

| 字段 | 值 |
|---|---|
| `iss` | Tool 的 client_id |
| `sub` | Tool 的 client_id |
| `aud` | Platform 的 Token URL |
| `iat` / `exp` | 当前时间 / 5分钟后 |
| `jti` | 唯一随机 ID（防重放） |

Platform 从 Tool 的 JWKS URL 获取 Tool 的公钥，验证这个 JWT，验证通过后返回：

```json
{
  "access_token": "token-uuid-xxx",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "https://purl.imsglobal.org/spec/lti-ags/scope/score"
}
```

**Step B：POST 成绩**

Tool 用 access_token 作为 Bearer token，向 lineitem URL 的 `/scores` 子路径 POST 成绩：

```
POST /lti/ags/lineitems/{id}/scores
Authorization: Bearer token-uuid-xxx
Content-Type: application/vnd.ims.lis.v1.score+json

{
  "userId": "42",
  "scoreGiven": 80,
  "scoreMaximum": 100,
  "activityProgress": "Completed",
  "gradingProgress": "FullyGraded",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

Platform 验证 Bearer token，找到 lineitem 对应的活动和用户，更新 `grades` 表。

**为什么比 LTI 1.1 好？**

- 用 REST+JSON 替代 XML，解析简单
- Token 有有效期，即使泄漏也有时间窗口
- Tool 不需要持有 Platform 的任何密钥；Platform 验证 Tool 的 JWT，Tool 只需公开自己的 JWKS

### 六、Tool 的 LTI Session 处理

Tool 在 Step 4 验证 id_token 后：

1. 将关键信息存入 `lti_sessions` 表（sub、lineitem_url、user_name、return_url 等）
2. 生成 session cookie，写入浏览器
3. 后续所有 `/exam`、`/exam/submit`、`/result` 请求都通过 cookie 找回 LTI 上下文

Tool 永远不知道用户在 Platform 里的密码，也不能代替 Platform 做任何操作。

---

## LTI 1.3 与 1.1 的关键区别

| | LTI 1.1 | LTI 1.3 |
|---|---|---|
| 认证基础 | OAuth 1.0a（对称签名） | OIDC + JWT（非对称加密） |
| 密钥类型 | 共享的 secret 字符串 | 公私钥对（RSA-2048） |
| Token 有效期 | 无（靠 timestamp+nonce） | JWT 有 exp 字段 |
| 成绩回传 | XML via Basic Outcomes | REST API via AGS |
| 深度链接 | 不支持 | 支持（工具可返回内容给平台） |
| 配置复杂度 | 低 | 高 |
| 安全性 | 中（secret 泄漏即失陷） | 高（私钥不传输） |

LTI 1.3 中，私钥永远不离开各自服务器。即使攻击者拿到公钥也无法伪造签名；即使截获 access_token 也会在有效期内过期。

---

## 本项目实现了哪些，省略了哪些

**实现了：**
- LTI 1.3 完整四步 OIDC Launch 流程
- JWT id_token 签名（RS256）与验证（从 JWKS URL 获取公钥）
- state / nonce 防 CSRF / 防重放（存库验证，10 分钟过期清理）
- AGS 成绩回传（JWT Bearer 获取 access_token，再 POST score）
- Platform 和 Tool 各自的 JWKS 端点（`/lti/jwks`）
- 标准 LTI Claims：`sub`、`roles`、`resource_link`、`context`、`deployment_id`、`lis`、AGS endpoint

**省略/简化了：**
- AGS Token 端点仅验证 JWT 签名和有效期，未严格检查 scope 权限
- access_token 存储为明文 UUID（生产环境应 hash 存储）
- 未实现 Deep Linking（LTI Content-Item Message）
- 未实现多 Deployment 支持（每个 Tool 只有一个 deployment_id）
- 多角色处理（只传了 `Learner`，未区分 `Instructor` 视角）
- 未实现 `/.well-known/openid-configuration` 标准发现端点（Demo 直接手填 URL）
