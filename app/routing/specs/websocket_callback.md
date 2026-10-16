# `app/routing/websocket_callback.py`

## ✅ 做了什么

`WebSocketAgentCallback`（LangChain `BaseCallbackHandler`）—— **一件事干两件**：
把 Agent 的中间过程**实时推到 WebSocket**，**同时给这一轮 LLM 调用记账**。

## 🟡 做到哪 / 缺什么

- **2 处产品代码引用 · 1 个测试文件提到**。
- 挂在 `/ws/agent` 那条链上（`app/routing/api_v1_rag.py` 的 `agent_websocket`）。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **记账用的是消息上的 `model_name`，按【真实模型】计价，⛔ 不许写死。**
   ⇒ 改这里时**别图省事填一个常量** —— 那会让「换模型 ⇒ 成本算错」**不报错**。
2. 🔴 **`record_from_response` 取不到用量时返回 `False` 并【静默跳过】—— 这是【有意】的。**
   ⇒ ⛔ **别把它改成抛异常**：拿不到 `usage_metadata` 的路径**本来就可能存在**，
   抛异常会把一次"这条链没记账"升级成"整条 WS 断掉"。
   ⚠️ 代价认了：**"没记账"与"记了 0"在日志里长得像** —— 真要查得看 `usage_metadata` 在不在。
3. ⚠️ **它是 `BaseCallbackHandler` 的子类** ⇒ 方法名（`on_llm_end` 等）是**框架约定**，
   ⛔ 改名字 = 回调**静默不触发**（没有报错）。
4. ⚠️ **改过 import**：`from billing.token_tracker import record_from_response`。
   📌 判据：`grep -n "^from" app/routing/websocket_callback.py`。

## 关联

- `app/billing/specs/token_tracker.md` —— `record_from_response` 的判据（`usage_metadata`）
- `app/routing/specs/api_v1_rag.md` —— `/ws/agent`（首帧认证）
