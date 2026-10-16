# `app/core/cache.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **可用（生产）** —— 🔴 **两类完全不同的东西住在同一份文件里**（embedding 缓存 + 对话历史） |
| **对外提供** | `redis_client` · `get_cache_key` · `get_cached_embedding` · `set_cached_embedding` · `INTERRUPTED_SUFFIX` · `get_chat_history` · `append_chat_history` · `persist_turn` · `HOT_QUERIES` · `warmup_cache` |
| **谁在用** | **embedding 一路**：`app/rag/embedding_client.py:5`（`:48` `get_cached_embedding` · `:71` `set_cached_embedding`）<br>**历史一路**：`app/routing/api_v1_rag.py:70`（`:721` `get_chat_history` · `:853`/`:890` `persist_turn`）· `app/routing/api_v1_agent.py:80`（`persist_turn` **9 处**）<br>**预热**：`app/main.py:29 → :713` |
| **测试** | ✅ `app/tests/test_cache_chat_history.py`（**14 条** · 含 `thread_id`/`status` 必填的守卫）· `app/tests/test_cancel_propagation.py` |

## ✅ 做了什么

**两个键族，同一个 Redis 客户端**：

| 键 | 内容 | TTL |
|---|---|---|
| `emb:<模型名>:<md5(text)>` | 文本的 embedding 向量（JSON） | 86400 秒 |
| `chat_history:<session_key(user, thread)>` | 对话消息列表（RPUSH） | 86400 秒 |

**`persist_turn` 是留痕的唯一入口** —— `done` / `cancelled` / `error` **三条出口共用这一段**。

## 🟡 做到哪 / 缺什么

| 缺口 | 说明 |
|---|---|
| 🔴 **两类东西没有共同的抽象** | embedding 缓存与对话历史只是**恰好用同一个 Redis**，⛔ 没有共享的接口/前缀管理。加第三种键时**没有地方收口** |
| ⚠️ **`HOT_QUERIES` 是硬编码的 10 个字符串** | 注释写「根据你的业务场景维护」—— 而它们**与业务无关**（`Python是什么？` / `苹果公司的主要产品` …）。⇒ 预热的是**别人的话题** |
| ⚠️ **`warmup_cache` 会真花钱** | 10 次 embedding 调用（失败被 `except` 住、**不阻塞启动**） |
| ⚠️ **没有"历史长度"之外的清理** | 只靠 24 小时 TTL；⛔ 没有条数上限（`lrange` 只**读**最近 10 条，⛔ 不**裁**列表） |
| ⚠️ **不做老键迁移** | 见 §⚠️ 第 5 条 |

## ⚠️ 看代码会误判的地方

> ⭐ 这一节是整份 spec 的价值所在 —— 前面两节读代码也能推出来，这一节**推不出来**。

### 1. 🔴 `lrange(key, -10, -1)` 是 **Redis 的语法**，⛔ **不是 Python 切片**

```python
data = redis_client.lrange(key, -10, -1)  # 只取最近10条（5轮问答）
```

⚠️ **Python 的 `lst[-10:-1]` 会少一个元素**（右端**不含**），Redis 的 `-1` **是含**的。
⇒ 两处都写 `-10` / `-1`，**语义不同** —— 本仓 `app/conftest.py:30` 专门为这一点留了注释
（说明有人在这里绕过一次）。

⇒ **判据**：读 **Redis 的** `LRANGE` 定义，⛔ 别按 Python 切片推。

### 2. 🔴 轮数换算：**10 条 = 5 轮** —— 因为一轮**写两条**

`persist_turn` 是**成对写**（`user` + `assistant`）。⇒ ⛔ 别把"最近 10 条"读成"最近 10 轮"。

⚠️ **成对写不是可选的**：只写答案不写提问 ⇒ 历史里出现一条**没有来由**的助手消息
⇒ 下一轮 prompt 里模型会看到一段**无源的回答**。

### 3. 🔴 `thread_id` 与 `status` 都是**必填关键字**，⛔ **不给默认值** —— 这是刻意的

| 形参 | 有默认值会怎样 |
|---|---|
| `status`（决策 3） | 留痕字段一旦有默认值，「忘了传」会**静默**变成假信号 —— 而这条 DEC 的**全部意义**就是「读的人能分辨这一轮说完了没有」 |
| `thread_id`（契约 C） | 有默认值 ⇒「忘了传」**静默**变成"都写进同一个桶" ⇒ 同一人开两个会话**互相串上下文** |

⚠️ **"关键字"这一半也有理由**：位置参数会让 `app/tests/test_cancel_propagation.py` 里
`for _, r, c in store` 式的解包 **`ValueError`**（那是**测试夹具**，⛔ 不该被产品签名牵动）。
🔒 守卫 ⇒ `app/tests/test_cache_chat_history.py`（`:120-124` `TypeError` 那三条 · `:132-136` 分桶 · `:149-153` 跨用户）。

### 4. 🔴 历史键**复用 `session_key`**，⛔ 不是新拼一种

`_history_key` = `f"chat_history:{session_key(user_name, thread_id)}"`。
⇒ `session_key` 是**长度前缀**的无歧义复合键（两个形参都必填、还带守卫用例）。
**再造一套 = 造第二个可能漂的实现。**

⚠️ **`from session_key import session_key` 放在【函数内】** —— 理由写在 docstring 里：
`app/core/cache.py` 在 **import 期**就建 `redis.Redis`，本仓多处（`conftest` / `check_route_auth`）
会**以各种 cwd 导入**它 ⇒ 顶层再拉一个模块会**扩大 import 期面积**。
⇒ ⛔ **别把它挪到文件头"整理一下"** —— 那是**有意的**（`warmup_cache` 里那句 `from embedding_client import get_embedding` 同一个写法）。

### 5. 🔴 **不做老键迁移** ⇒ 升级后会出现一次「历史看起来丢了」

改前写的是 `chat_history:{user}`（之前），现在读的是 `chat_history:{session_key(...)}`
⇒ ⚠️ **老键 24 小时后自行过期，本刀只保证"新写的读得回"。**

⇒ **症状**：升级后头一天，用户会觉得"我之前聊的没了"；**第二天自己就好了**（老键过期了）。
⚠️ **别把这当成 bug 去加迁移** —— 那是**明文的取舍**；但也**别以为它没发生**。

### 6. 🔴 老条目没有 `status` ⇒ 读回时补 `"done"` —— 这是**对的，⛔ 不是猜**

**归一化只在这一个读取点**（`get_chat_history`）。
**为什么补 `done` 是对的**： 之前**根本不存半截** ⇒ 老条目**只可能是完整答案**。
⇒ ⛔ **别在写入侧也补**（那会让"没传 status"变得合法，正是上一条要防的）。

### 7. 🔴 `persist_turn` 的**空答案 ⇒ 什么都不写** —— 连**提问**也不写

```python
text = answer or ""
if not text.strip():
    return          # ⚠️ 整轮跳过，不是"只跳过答案"
```

⇒ 理由：空的助手消息只会**污染下一轮 prompt**（决策 4）。
⚠️ **后果**：一瞬间的失败会让**这一问一答在历史里完全不存在** —— 用户问的那句话也一起没了。
⇒ 而 `status="error"` 的**告警**是另一条路（写入侧仍会记 error）… ⚠️ **只有"空答案"这一种才整轮丢**：
非空答案 + `status != "done"` ⇒ 存半截**并**追加 `INTERRUPTED_SUFFIX`。

### 8. 🔴 `INTERRUPTED_SUFFIX` 的**唯一来源在本文件**（从 `api_v1_rag.py` 搬来）

⚠️ 它那句「…（本次回答被中断，以上为已生成部分）」是**写给模型看的**（会进下一轮 prompt）。
⇒ ⛔ **别在两处各写一遍** —— 那句话**会漂**，而漂了**不会报错**（共用它）。

### 9. ⚠️ `get_cache_key` 用 **MD5** 当键 —— 只做去重，⛔ 不是安全用途

⚠️ MD5 **有已知碰撞** ⇒ 理论上**两段不同文本可能命中同一个缓存**，症状是
「这段文本检索出来的向量像是**别的内容**的」—— 且**不报错**。
⇒ 概率极低（需刻意构造），但 ⛔ **别把"用了 MD5"读成"有完整性保证"`。**

### 10. ⚠️ `redis_client` 是**模块级 import 期构造**的

`redis.Redis(...)` 在 **import 时**就建对象（⚠️ `redis-py` 是**惰性连接**，此刻**不真的连**）。
⇒ **导入 `cache` 不需要 Redis 在跑**；但**第一次用**才连。
⚠️ 这也是本文件那些"函数内导入"存在的原因（§⚠️ 第 4 条）。

### 11. ⚠️ 缓存**不知道** embedding 属于哪个用户

`emb:<模型名>:<md5(text)>` **不含 user_id** ⇒ **跨用户共享**。
⚠️ **这是对的**（同一段文本的向量与用户无关，共享省的是真金白银），
但 ⛔ **别把它读成"缓存泄露"** —— 泄露的是**向量**，不是文档；**检索侧的隔离在 SQL 的 `WHERE` 里**。

### 12. 🔴 键里**必须**有模型名（加 · §2.1 的入口在缓存这一侧）

**改前**：`get_cache_key(text)` ⇒ `"emb:" + md5(text)`，**模型名不在键里**，TTL 24 小时。
⇒ **切完 embedding 模型后，同一句话会命中【旧模型】的向量**，而它**不报任何错**
（维度不同时 pgvector 会报；**维度恰好相同时连报都不报**）⇒ 检索结果与问题无关，且看不出来。

**改后**：`get_cache_key(text, model)` —— `model` 是**必填位置形参，⛔ 没有默认值**。
⚠️ 「漏传要 `TypeError`」是**有意**的：给默认值 ⇒ 忘了传就**静默落进同一个桶**
（与 `access/session_key.session_key` 同一条取向）。
⚠️ **键格式变了 ⇒ 旧 `emb:*` 键自然失效**（不必手工 flush，24h 后也会没）。
🔴 **`rag/query_rewriter.py` 有它自己的 `_get_cache_key(prefix, text, extra)`** ——
那是**改写缓存**、与 embedding 无关 ⇒ ⛔ **别顺手一起改**。

## 关联

| 文档 | 说明 |
|---|---|
| `app/access/specs/session_key.md` | 历史键复用的那个复合键（长度前缀 · 无歧义） |
| `app/rag/specs/embedding_client.md` | embedding 缓存的两个消费点（`:48` / `:71`） |
| `app/routing/specs/api_v1_rag.md` | 流式留痕的调用点（`persist_turn` 三条出口） |
| `app/routing/specs/api_v1_agent.md` | `persist_turn` **9 处**（Agent 各条路径） |
| `app/specs/main.md` | `warmup_cache` 的启动调用（`:713`） |
