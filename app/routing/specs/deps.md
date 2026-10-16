# `app/routing/deps.py`

| 项 | 内容 |
|---|---|
| **状态** | 🟡 部分可用 —— **HTTP 侧完整**；**WebSocket 侧 2026-10-05 才补上**（此前 WS 整条裸奔） |
| **对外提供** | **HTTP 认证**：`get_current_user`（API Key）· `get_current_user_jwt`（JWT）· `get_current_user_hybrid`（两者皆可）· `require_admin`（管理员）· `verify_jwt_token`（纯函数）· `oauth2_scheme`<br>**WebSocket 认证**（🆕）：`require_ws_user`（依赖）· `resolve_ws_identity`（**纯函数**）· 常量 `WS_AUTH_TIMEOUT_SECONDS` / `WS_CLOSE_POLICY_VIOLATION`(1008) / `WS_CLOSE_INTERNAL_ERROR`(1011) |
| **谁在用** | `app/routing/api_v1.py` · `app/routing/api_v1_agent.py` · `app/routing/api_v1_rag.py`（含 WS 那两条路由）· `app/main.py` —— ⚠️ 见  的 `AUTH_NAMES`**就是靠本文件的函数名认人的** |

## ✅ 做了什么

- HTTP 三种凭据（API Key / JWT / hybrid）都能解析成 `user_name`；`require_admin` 再叠一层角色。
- **WS 首帧认证**：`require_ws_user` 挂在 WS 路由上 ⇒ 客户端连上后**第一帧**发
  `{"type":"auth","api_key":…}`（或 `{"token":"<JWT>"}`）⇒ 通过才回 `{"type":"ready","user":"…"}`，
  不通过以 `close(1008)` 关闭，**端点体一次都不执行**。
- 认证失败**分清是谁的问题**：凭据不行 = **1008**；认证服务自己不可用（库连不上）= **1011**。

## 🟡 做到哪 / 缺什么

  ⚰️ `/api/v1/ws/test` **同日已删**（§十 · 纯回声测试桩 · 消费者 = 0）。
  ⇒ 基线里那条 WS **已消掉**（现只剩 HTTP 的 `/api/v1/`）。
- ⬜ **WS 的会话桶仍是"每连接"** —— `thread_id = f"ws-{uuid4}"`，重连 = 换桶。
  见  遗留·2（全局日级熔断 B11 仍罩着，故未失控）。
- ✅ ~~**`verify_api_key` 的 `get_db()` 没有 try**~~（遗留·4 ⇒ 本表）
  —— **2026-10-05 已修**（批 3）：库异常 ⇒ 抛 **`AppException(SERVICE_UNAVAILABLE)`** ⇒ **503**。
  🔴 ** 原文那句「500 而不是 401」【两个都不是答案】** —— 理由见本节实施计划与。
  ⚠️ **WS 侧本来就用 1011 绕过了这个坑；现在 HTTP 侧的口径与它对齐了**（此前两边不一致）。

## ⚠️ 看代码会误判的地方

| 看代码容易以为 | 实际 |
|---|---|
| 🔴🔴 **「`verify_jwt_token` 解出名字就够了 —— 令牌没被撤这回事」** | ⛔ **不够** —— JWT 是**无状态**的：只验签名与过期，**⛔ 不查库** ⇒ **撤销一个账号，他手里的令牌在过期前一直有效**（access 15 分钟，**refresh 7 天**）⇒ 「撤销」会退化成"**下次登录才生效**"。<br>✅ **2026-10-15 起**：解码后**再过一道 `access.auth.login_account_is_live`**（§二·⑥）⇒ **撤销最多 30 秒内生效**。<br>⚠️ **两个入口都要堵** —— 只堵这里，那张 `refresh` 还能**一直换新令牌**；`/auth/refresh` 那处**也得过同一道门**。<br>🔴 **而那道门的判定口径别想当然**：「查不到这一行」**不拦** —— 写成"拦"会让 **CI（无 PG）与全部自签 JWT 的用例一起死**，而换来的保护是零（能自签的人已经有 `JWT_SECRET_KEY` 了）。详见 `access/login_users.py::login_account_blocked` 的 docstring。 |
| 🔴 **「WS 路由也能用 `get_current_user_hybrid` 那套」** | ⛔ **送不上来** —— `new WebSocket(url)` **不能自定义请求头**（浏览器 API 限制）⇒ `X-API-Key` / `Authorization` 客户端**根本发不出**。⚠️ 照抄 HTTP 依赖的形态**看着像接了鉴权**，实际是把所有人都挡在门外（或更糟：以为挡了其实没有）。⇒ WS 只能走**首帧 / 查询串 / 子协议**三条路，本轮选**首帧**。 |
| 🔴 **「`require_ws_user` 里怎么还 `accept()` 了一次，端点里那次可以删」** | ✅ **端点里那次【必须】删** —— 星型 1.6.0 的 `WebSocket.accept()` **不幂等**，第二次会 `RuntimeError: Expected ASGI message "websocket.send" …, but got 'websocket.accept'`（实测）。本文件里那次是**唯一**一次。 |
| 🔴 **「失败直接 `close()` 不就行了，何必抛异常」** | ⛔ **会连发两帧 close** ⇒ `RuntimeError: Cannot call "send" once a close message has been sent.`。<br>星型自己会接住 `WebSocketException` 并 `close(code=…, reason=…)`（`ExceptionMiddleware.websocket_exception`，靠 **MRO** 命中 —— `fastapi.WebSocketException` 是 `starlette.exceptions.WebSocketException` 的子类，实测）。<br>⚠️ **只能二选一**：自己 close，**或**抛异常让星型 close。 |
| 🔴 **「先 `close()` 再 `accept()` 也一样，反正都是关」** | ⛔ **两回事**：`accept()` **之前**关 ⇒ 浏览器只看到**握手失败**，**拿不到关闭码**（实测）；`accept()` 之后关 ⇒ 前端 `onclose` 能读到 `code=1008` 与 `reason`。<br>⇒ 顺序是**用户可见行为**的一部分，⛔ 不是实现细节。 |
| 🔴 **「1008 和 1011 都是失败，随便报一个」** | ⛔ **对客户端是相反的处置**：1008 = 你的凭据不行（**换 key**）；1011 = 我这边不行（**重试，别换 key**）。<br>把库抖动报成 1008，等于**告诉用户去换一把没问题的 key** —— 他换了还是连不上，且**永远查不到原因**。 |
| 🔴 **「`deps.py` 里鉴权 fail-closed，那 `token_tracker` 怎么 fail-open，统一一下」** | ⛔ **故意相反，别统一**：这里是**安全边界**（放错了 = 匿名进门）；那边是**成本控制**（挡错了 = 全站停摆）。`token_tracker.get_session_token_usage` 的 docstring 里写着同一条。 |
| 🔴 **「中间件不是已经挡过了吗」** | ⛔ **挡不到 WS** —— `app/main.py` 那两个是 `BaseHTTPMiddleware`，**只看 `scope["type"] == "http"`**。⇒ WS 的鉴权**只能挂在路由自己的依赖上**，这也是它曾经整条裸奔的原因。 |
| 🔴 **「改了个鉴权函数名而已，跑一下测试就行」** | ⛔ **还要改  的 `AUTH_NAMES`** —— 那个门是靠**函数名字符串**认人的，**名单漂了它会静默放过**（实测：把 `require_ws_user` 从名单里拿掉，两条 WS 立刻被判为"无鉴权"）。 |

# ✅ 实施计划 · 批 3 · （2026-10-05 立 · **同日做完**）

> **结果**：`app/tests/test_auth_db_unavailable.py` **22 条全绿** · 全量 **654 passed** · **变异自证 17/17**
> （相关 **7 条**；含"**把捕获写宽**"与"**中间件不再照 `None` 办事**"两类）。
> 📄 **决策（503 的来历 + 四个落点的分工 + 反悔成本）**

## 🔴 先纠一处：**正确答案既不是 401、也不是 500**

 行原文写「库一抖动 ⇒ **500 而不是 401**」。⛔ **别照这个字面修** ——
按本文件上面那条 **1008 / 1011**（WS 侧已裁、作者过目）：

- **凭据不行** ⇒ 让用户**去换 key**
- **认证服务不行** ⇒ 让用户**重试，⛔ 别换 key**

库连不上时，**我们并不知道那把 key 是真是假** ⇒ 报 401 = **替用户断言「你的 key 坏了」**，
而那正是 1008/1011 那条禁止的**归错因**（他去换一把没问题的 key，然后照样连不上）。
⇒ **HTTP 侧的对应值是 `503 SERVICE_UNAVAILABLE`** —— `app/core/exceptions.py` 里**早就有这个码，
只是从来没被用过**。

## 落点：**异常在【源头】抛一次，往哪倒由调用点各自决定**

| 层 | 处 | 取向 | 动作 |
|---|---|---|---|
| 源头 | `auth.verify_api_key` | —— | DB 异常 ⇒ **抛 `AppException(SERVICE_UNAVAILABLE)`**，⛔ **不返回 `None`** |
| 安全边界 | `deps.get_current_user` | **fail-closed** | **不捕获** ⇒ 冒泡 ⇒ 全局处理器 ⇒ **503** |
| 保护措施 | `main.resolve_rate_limit_identity` | **fail-open** | 捕获 ⇒ **本请求不参与用户级限流**（＋ `logger.error`） |
| 成本控制 | `main.resolve_quota_identity`（🆕，`QuotaMiddleware` 调它） | **fail-open** | 捕获 ⇒ 身份为 `None` ⇒ **原样放行**（与该中间件既有的「未识别身份就放行」一致） |

⚠️ **第 4 行原来是内联在 `QuotaMiddleware.dispatch` 里的**，本次**抽成模块级函数** `resolve_quota_identity`
—— 理由与 `resolve_rate_limit_identity` 逐字相同：**让这段逻辑能被单测**（中间件本体要连库）。
⛔ **行为逐字保留**，包括「**先试 API Key、拿不到身份再试 JWT**」那个顺序（抽错会静默改变带双凭据客户端的配额身份）。

### 为什么是「不参与限流」，⛔ 不是「降级到匿名桶」

降级到匿名桶 = **把库抖动算到用户头上**（`anonymous` 是**一个** 20 容量 / 3 每秒的桶
库一挂，所有带 key 的人都挤进去 ⇒ **大面积假 429**）——
这正是 1008/1011 那条禁止的**归错因**的限流版。

⇒ `resolve_rate_limit_identity` 的返回值**多一个 `None`**：「**识别不了**」≠「**确实匿名**」。
✅ **原有 8 条用例（`app/tests/test_rate_limit_identity.py`）一条都不用改，`git status` 干净、8 passed**
—— 它们用的是 `_no_db`（**库正常**）那条路径
⇒ `None`（判不了）与 `"anonymous"`（判了就是匿名）**没有互相污染**。

⚠️ **捕获必须【按类型】**（⛔ 不是 `except Exception`）——
`app/tests/test_rate_limit_identity.py` 的 `_no_db` fixture **靠抛 `AssertionError` 抓「谁碰了库」**；
写宽了会把它一起吞掉 ⇒ **那道守卫静默失效**（那边同理）。
📌 **实测**：把 `auth` 那处改成 `except Exception` ⇒ `test_非数据库异常必须照样冒泡` 转红（变异 `N9-b`）。

## 用例（🆕 `app/tests/test_auth_db_unavailable.py` · 22 条 · 离线 · ⛔ 不连库/Redis）

**第一组 · 源头与安全边界**

| # | 断言 |
|---|---|
| 1 | `verify_api_key` 遇库异常 ⇒ **抛 `AppException(SERVICE_UNAVAILABLE)`**（⛔ **不是返回 `None`**） |
| 2 | 同族失败（**池耗尽** `pool.PoolError`）⇒ 同一个应答 |
| 3 | 🔒 **防写宽**：`TypeError`（代码 bug）**必须照样冒泡** |
| 4-6 | 🔄 **反向守卫**：库**正常**时 —— 查不到 ⇒ `None`；key 有效 ⇒ 用户名；**已过期** ⇒ `None` |
| 7 | `deps.get_current_user` ⇒ 冒出来的是 `SERVICE_UNAVAILABLE` · 状态码 **503**（⛔ 不是 401、不是 500） |
| 8 | 🔄 **反向守卫**：**没带** key 仍是 **401**（别被这次改动带偏） |

**第二组 · 两个身份解析函数**

| # | 断言 |
|---|---|
| 9 | `resolve_rate_limit_identity` 遇库异常 ⇒ 返回 **`None`**（⛔ 不是 `"anonymous"`） |
| 10 | 🔄 库正常但**验不过** ⇒ **仍是 `"anonymous"`**（既有裁定不变） |
| 11 | 🔒 `resolve_rate_limit_identity` 遇到别的异常**不许吞** |
| 12 | `resolve_quota_identity` 遇库异常 ⇒ 返回 `None`（⇒ 中间件跳过额度检查） |
| 13 | ⚠️ **必须响**：跳过额度检查时**留下 ERROR 日志** |
| 14-15 | 🔄 `resolve_quota_identity`：验不过 ⇒ `None`；key 有效 ⇒ 用户名 |
| 16 | 🔄 没带任何凭据 ⇒ `None` |
| 17 | 🔒 **行为保持**：API Key **验不过还会去试 JWT**（抽取时最容易丢的顺序） |
| 18 | 🔒 `resolve_quota_identity` 遇到别的异常**不许吞** |

**第三组 · 接线（⚠️ 只测函数**不够**）**

> 🔴 **为什么单列**：「函数返回 `None`」**不等于**「中间件照 `None` 办了事」——
> 把 `if user_name is None: return await call_next(request)` 那两行删掉，**第二组照样本全绿**。
> ⇒ 这组直接调 `dispatch`（`call_next` 用替身），判据是「**碰了不该碰的东西就炸**」：
> 把 `user_limiter` / `get_token_budget_info` 换成**一碰就抛**。

| # | 断言 |
|---|---|
| 19 | `RateLimitMiddleware` 拿到 `None` ⇒ **不碰用户桶**、原样放行（判据：桶的两个方法都换成"一碰就炸"） |
| 20 | 🔄 身份**正常** ⇒ 用户桶**照常生效**（⛔ 别写成"无脑放行"） |
| 21 | `QuotaMiddleware` 拿到 `None` ⇒ **不查库**、原样放行 |
| 22 | 🔄 身份**正常** ⇒ **照常查预算**（⛔ 别把额度层整层关掉） |

---

## 关联

 遗留·1（WS 无鉴权这条债的**出处**）·
`app/routing/specs/api_v1_rag.md` · `app/specs/main.md` · `app/billing/specs/token_tracker.md`
