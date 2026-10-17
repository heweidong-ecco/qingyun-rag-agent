# `app/core/config.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **可用** —— **全仓环境变量的唯一入口**（规范要求⛔ 不许别处 `os.getenv`）。⚠️ 但它有**两处 import 期副作用** |
| **对外提供** | 22 个常量（PG / Redis / JWT / 登录 / LLM 四组）· `IS_DOCKER` · `validate_config()` |
| **谁在用** | 几乎所有模块。已核的：`app/core/db.py:4` · `app/core/cache.py:4` · `app/access/auth.py:9` · `app/alembic/env.py:13`。⚠️ `docs/契约/环境变量.md:145` 记了一处**例外**：`app/agent/memory_store.py:33` 直读 `os.getenv` |
| **测试** | 🔴 **专属用例零条** —— `app/test_config.py` **不存在** |

## ✅ 做了什么

**一处读环境变量，其他模块从本文件 import**（明文写在本文件 docstring 与 `docs/规范/开发规范.md` §116）。

**两类变量，两种口径**：

| 类 | 缺了会怎样 |
|---|---|
| **非敏感**（有合理默认值） | `POSTGRES_USER` / `POSTGRES_DB` / `POSTGRES_HOST` / `POSTGRES_PORT` / `REDIS_HOST` / `REDIS_PORT` / `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` / `LOGIN_USER_NAME` … |
| 🔴 **敏感**（**禁止默认值**） | `DASHSCOPE_API_KEY` · `POSTGRES_PASSWORD` · `JWT_SECRET_KEY` · `LOGIN_PASSWORD` · `LLM_API_KEY` ⇒ `validate_config()` 缺一个就 **`EnvironmentError`** |
| ⚠️ **可选（设了才存在）** | `TEST_USER_PASSWORD` ⇒ 不设 = **`test_user` 账号不存在**（fail-closed，⛔ 不留默认口令） |

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| ⚠️ **`memory_store.py:33` 绕过了本文件** | 直读 `os.getenv("DASHSCOPE_API_KEY")` ⇒ ⛔ **拿不到本文件的 `IS_DOCKER` 覆盖**（`docs/契约/环境变量.md:145` 已记） |
| 🔴 **零专属测试** | "缺敏感变量要拒启动"这条**没有任何用例钉着**（判据只能手跑，见 §⚠️ 第 4 条） |
| ⚠️ **`last_checked` 式的"谁在用"没有机制保证** | 「不许别处 `os.getenv`」是**规范文字**，⛔ 没有门在拦（`memory_store.py` 就是漏网的一处） |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. 🔴 环境变量的名字是 **`DOCKER_ENV`**，⛔ **不是** `IS_DOCKER`

```python
IS_DOCKER = os.getenv("DOCKER_ENV", "false").lower() == "true"
```

⇒ **`IS_DOCKER` 只是本模块内的 Python 名**。往 `.env` 里写 `IS_DOCKER=true` —— **一点效果都没有**，
而且**不报错**（拼错的变量名在 `.env` 里是静默无效的，本仓反复记的那一族）。

🔴 **判据（可打印）**：
```bash
grep -rn 'IS_DOCKER\|DOCKER_ENV' --include='*.py' --include='*.yml' .
grep -n 'DOCKER_ENV' docker-compose.yml     # ⇒ :22  DOCKER_ENV=true
```

### 2. 🔴 文件**末尾**有一处 import 期覆盖 —— 它会**盖掉你在 `.env` 里设的值**

```python
# ==================== 本地开发覆盖 ====================
if not IS_DOCKER:
    POSTGRES_HOST = "localhost"
    REDIS_HOST = "localhost"
```

⚠️ **不是"设默认值"，是【无条件改写】** —— 只要 `DOCKER_ENV` 不是 `true`，
**这两个变量在 `.env` 里写了什么都会变成 `localhost`**。

🔴⇒ 症状是：「**我在 `.env` 里把 `POSTGRES_HOST` 改成远程库，怎么还是连 localhost？**」
⇒ 因为**确实被改掉了**，而且**没有任何日志**说这件事发生了。

⚠️ **别把它读成 bug** —— 有意的（本地开发连本机 PG/Redis）。但它是一条**静默覆盖**，
所以 `docs/契约/环境变量.md:83` 专门写明了它。

🔴 **判据**：`grep -n 'if not IS_DOCKER' app/core/config.py` ⇒ 命中；读它后面那两行。

### 3. 🔴 `LLM_API_KEY` **没有 `DASHSCOPE_API_KEY` 兜底** —— 旧写法是个**静默错配**

旧版是 `LLM_API_KEY = os.getenv("LLM_API_KEY") or DASHSCOPE_API_KEY`。
**问题**：那会把 **embedding 的 key** 拿去请求 **DeepSeek 端点** —— **静默错配**（能跑，但用的是别人的 key）。

：**必须显式给 `LLM_API_KEY`**。
⇒ ⛔ **别顺手把 `or DASHSCOPE_API_KEY` 加回来"做容错"** —— 那正是被撤掉的写法。

⚠️ **embedding 仍固定走 `DASHSCOPE_API_KEY`**（**端点**不变），本文件那段只管**生成/对话**模型。
⇒ **两个 key 分工不同，⛔ 别合并成一个。**
🔴 **更正**：`text-embedding-v2` 这个**模型名**不再写死 —— 它现在由
`EMBEDDING_MODEL_MAIN` 管（本文件 §3 新增那一行）。
🔴 **（B 段）默认值也改了**：`text-embedding-v2` ⇒ **`qwen3.7-text-embedding-flash`**。
   ⚠️ **那不只是文档债** —— 本服务容器里 `.env` **不进镜像**，平台没设这个变量时**会回落到默认值**
   ⇒ 默认值若不与表定义（`vector(1024)`）同代，**检索当场坏**（§2.1）。
⚠️ **换它必须连【维度 + 表定义】一起改**（§2.1）。
🔴 **还有第二处 embedder**：`app/agent/memory_store.py` 的 mem0 embedder ——
它**不走 `validate_config`**、key 也曾经是**裸 `os.getenv`** ⇒ 两处**同一个变量**。

### 4. 🔴 「启动时校验」这句话**比实际时机晚** —— 先炸的往往是 **import 期**

`validate_config()` 在 `app/main.py:709`（startup）被调。
⚠️ **但**：`make_llm()` 在 **import 期**就判 `LLM_API_KEY`，拿不到就抛
**点名它**的 `EnvironmentError`（改的；改前是把 `None` 递给 `ChatOpenAI`
⇒ SDK 那句通用话提的是 **`OPENAI_API_KEY`**，而本仓根本不用那个变量）。

⇒ **不是"更安全"，是"更难定位"**：错误发生在「导入某个模块」时，堆栈指向 **import 那一行**，
看着像"这个模块坏了"，实际是**环境变量缺了**。

🔴 **判据**：`grep -n 'validate_config' app/main.py` ⇒ `:709`；`grep -n 'LLM_API_KEY' app/core/llm_factory.py` ⇒ 报错点名处。

### 5. ⚠️ `LOGIN_USER_NAME` 默认 `"admin"` —— 与 `permission.py` 的特判**是同一个字面量，但两处各写一遍**

| 处 | 写法 |
|---|---|
| `app/core/config.py:35` | `LOGIN_USER_NAME = os.getenv("LOGIN_USER_NAME", "admin")` |
| `app/access/permission.py` | `if user_name == "admin":` ← **硬编码字面量** |

⇒ ⛔ **别把 `LOGIN_USER_NAME` 改成别的值**：登录用户名会变，而**管理员判定不会变**
⇒ 出现"登录的是 `root`，但系统里没有 `root` 这个管理员"，且**两边都不报错**。
⇒ 本仓原话：**「同一个名字两个来源必然漂移，而漂移是静默的」**。

### 6. ⚠️ `load_dotenv` 默认**不覆盖**已存在的环境变量

```python
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))   # ⇒ 仓库根 .env
```
`override` 默认 `False` ⇒ 🔴 **shell / 容器注入的真实环境变量优先，`.env` 只补空位**。
⇒ 调试时"我改了 `.env` 怎么没用" —— 先看**环境里是不是已经有一个**（判据：容器里 `printenv POSTGRES_PASSWORD`）。

⚠️ **容器里那个 `.env` 路径可能根本不存在** ⇒ `load_dotenv` **静默 no-op**，全靠真实环境变量。

### 7. ⚠️ `LLM_MODEL_FAST` 与 `LLM_MODEL_CHAT` 默认值**相同**

两个都默认 `deepseek-v4-flash`。⇒ **"快模型"与"对话模型"现在分不出来**。
⚠️ 但**它们在 `llm_factory` 里是两条不同的轴**（模型轴 + 长度轴）—— ⛔ **别因为默认值相同就以为可以合成一个**。

### 8. ⚠️ `POSTGRES_PORT` 默认 `5432`，而本机那套 PG 跑在别处

⚠️ 本仓 `.env` 里是显式给的（⛔ 别拿默认值当"实际连的端口"）。
⇒ **拿不准就连上去问**（`docs/契约/环境变量.md` §198 那种做法），⛔ 别凭默认值推断。

### 9. 🔴 `TRUST_PROXY_HEADERS` 默认 `false` —— 那是**安全边界**，⛔ 不是"功能没做好"


🔴 **为什么默认必须是 `false`**：它决定**要不要信**客户端的 `X-Forwarded-For` / `X-Real-IP`。
- 在**真代理后面**（如魔搭创空间）时，代理会**覆盖**客户端塞的那一跳 ⇒ 头里第一跳是**真的**；
- 在**本机 / 自建**部署下**没有代理** ⇒ 那个头**谁都能伪造** ⇒ 默认就信它的话，
  谁都能填一个 IP **换一个全新的限流桶** —— **比不做按 IP 还糟**。

⇒ 所以**改了 `.env` 却没生效**（按 IP 没分桶）**不是 bug**，是**开关没开**。
⚠️ 它**只影响限流分桶**（`main.resolve_client_ip`），⛔ 不影响鉴权、⛔ 不影响配额身份。

🔴 **判据（可打印）**：`grep -n 'TRUST_PROXY_HEADERS' app/core/config.py app/main.py`
📌 有门盯着默认值：`app/tests/test_rate_limit_ip.py::test_the_switch_defaults_to_off_in_source`。

## 关联

| 文档 | 说明 |
|---|---|
| `docs/契约/环境变量.md` | ⭐ **逐个变量的契约**（含 `DOCKER_ENV` 覆盖 · `memory_store.py` 那处例外） |
| `docs/规范/开发规范.md` | 「环境变量从 `app/core/config.py` 导入」那条规矩 |
| `app/access/specs/auth.md` | `LOGIN_USER_NAME` / `LOGIN_PASSWORD` / `TEST_USER_PASSWORD` 的**三个消费口** |
| `app/access/specs/permission.md` | §⚠️ 第 5 条的另一半（`"admin"` 那个硬编码字面量） |
| `app/core/specs/llm_factory.md` | `LLM_API_KEY` 的**报错点**与两条轴 |
