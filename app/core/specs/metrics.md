# `app/core/metrics.py`

| 项 | 内容 |
|---|---|
| **状态** | ✅ **可用** —— 4 个指标，全部走 `prometheus_client` 默认 REGISTRY<br>✅ **2026-10-03**：新增 **`stream_cancelled_total`** —— 它是判据③**唯一可执行的观测对象** |
| **对外提供** | **`GET /metrics`**（`app/main.py:450`，Prometheus text format）· 4 个模块级函数/对象 |
| **谁在用** | `app/main.py`（中间件 `log_and_track_request` 记 `track_request` / `REQUEST_IN_PROGRESS`）·<br>`app/routing/api_v1_rag.py:737` · `app/routing/api_v1_agent.py:244`（各一条流式端点调 `track_stream_cancel`） |

## ✅ 做了什么

| 指标 | 类型 | 标签 | 谁来记 |
|---|---|---|---|
| `api_requests_total` | Counter | `endpoint` `method` `status` | `main.py` 中间件（成功路径） |
| `api_request_duration_seconds` | Histogram | `endpoint` `method` | 同上 |
| `api_requests_in_progress` | Gauge | — | 同上（进/出各一次） |
| **`stream_cancelled_total`** | **Counter** | **`endpoint`** | **流式端点自己的 `finally`**（`track_stream_cancel()`） |

## 🟡 做到哪 / 缺什么

- ⬜ **`/metrics` 无鉴权** —— `main.py:110` 的免鉴权白名单里有它（`/health` `/ready` `/metrics`）
- 🔴 **有指标 ≠ 有面板** —— Grafana provisioning 缺口仍在（`ROADMAP` 阻塞项 #2）：
  **固定看板里没有这几格的任何一格**，新指标**不会自动出现**
- ⬜ **中间件只记「成功走到 `call_next` 返回」的那些请求** —— 鉴权失败等在中间件里提前返回的路径**不记**（本轮未核）
- ⬜ 无测试覆盖本文件

## ⚠️ 看代码会误判的地方 ⭐

| 看代码会以为 | 实际 |
|---|---|
| 🔴 **「有 Prometheus 指标 = 成本/用量可观测」** | ⛔ **不是** —— 上面 3 个全是 **HTTP 层**（请求数 / 延迟 / 进行中），**一个 token 都不数**（`grep -ci token app/core/metrics.py` ⇒ 0）。<br>本仓的**用量记账**走 `app/billing/token_tracker.py` → PG `token_usage_logs`，**与 Prometheus 无关**，且**只在生成结束后整笔写**。 |
| 🔴 **「那用 `token_usage_logs` 当"生成停没停"的判据就行」** | ⛔ **流式路径上看不到任何东西** —— 生成阶段**一行都不写**（§①）。<br>⇒ **这正是当初"判据③无法证伪"的根因**：验证对象根本不存在。 |
| ⚠️ **「`/metrics` 里没有某指标 = 功能没跑」** | ⚠️ **带标签的 Counter 在第一次 `.labels().inc()` 之前【不出现】** —— 所以"取消前查不到 `stream_cancelled_total`"是**正常**的，⛔ 不是没生效（真服务实测第一屏就是空的）。 |
| ⚠️ **「`stream_cancelled_total` 涨了 = 上游停止计费了」** | ⛔ **证不到那一步** —— 它证的是「**我们的生成器在跑完之前被终止了**」（`outcome == "cancelled"`）。<br>上游账单侧没有观测面（本机无 DashScope 出账）⇒ ⛔ 别把它说成"账单停了"（§遗留·2）。 |

## 关联

本仓的决策记录（⛔ 不随产品仓提供）
`app/routing/specs/api_v1_rag.md` · `app/routing/specs/api_v1_agent.md` · `app/billing/specs/token_tracker.md`（**另一条链**，别混）
