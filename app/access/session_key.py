"""会话键 —— **把身份拼进 checkpoint / 会话 id**（丙段）。

## 为什么要有这个模块

4 张图（`agent_graph` · `checkpointer_agent` · `advanced_agent` · `mcp_agent`）、
**7 处** `{"configurable": {"thread_id": thread_id}}` —— **key 里都没有人**。
而 `thread_id` 在 **6 条端点上**的默认值就是 `"default"`
（`api_v1_agent.py` 的 `:112` `:157` `:389` `:428` `:484` `:735`）
⇒ **两个用户都用默认值 ⇒ 共用一个 checkpoint 桶**。
而 reducer 是 `Annotated[List, operator.add]` ⇒ **消息是 append 的**
⇒ B 的提问接在 A 的历史后面，**模型两边的都看得到**。

**不是"理论上会串"，是默认就串** —— 默认值本身就是 `"default"`。

## 对照：花费那条轴【没有】这个问题

`check_session_token_budget(user_name, thread_id)` 的 key **含 `user_name`**（`token_tracker.py:847`）
⇒ 同一个仓里两套口径：**花钱的账按人算，对话记忆不按人算。**

## 为什么要"长度前缀"而不是 `f"{user}:{thread}"`

朴素拼法**有歧义**：`("a", "b:c")` 与 `("a:b", "c")` 都会拼成 `"a:b:c"`
⇒ **两个不同的人共用一个桶**。
而用户名由 `create_user_api_key` 创建，**没有字符校验** ⇒ 含 `:` 是可能的
⇒ 在一个**专门修隔离**的改动里留一条歧义拼法，等于没修。
⇒ 用**长度前缀**：用户名占多少字符是写死的 ⇒ **切分点唯一** ⇒ 无歧义。

裁定 ⇒ （决策 1 · 丙段）
模块 spec ⇒ `app/access/specs/session_key.md` · 判据 ⇒ `app/tests/test_session_key.py`
"""


def _require_identity(value, where: str) -> None:
    """fail-closed：缺身份 ⇒ **抛错**，不是"当成匿名 / 退回默认"。

    与 `db._require_identity` / `bm25_index._require_identity` **同一条约定**
       （§六 ③）：本仓已裁定 fail-open 是根因，
       所以"传空就当默认桶"这条路人**在这里也要堵死**。
    """
    if not value:
        raise ValueError(
            f"session_key 需要 {where}（非空）—— 传空就等于「退回默认桶」，"
            f"那是本仓已裁定的 fail-open 反模式（§二 根因 / §六 ③）"
        )


def session_key(user_name: str, thread_id: str) -> str:
    """把 `user_name` 与 `thread_id` 拼成一个**无歧义**的会话键。

    **两个形参都必填、都没有默认值**（同 `hybrid_search(..., *, user_id)`）——
       有默认值 = "可以忘记传" = 还是 fail-open；漏传即 `TypeError`，
       不是静默串号。（守卫 ⇒ `app/tests/test_session_key.py::test_both_params_are_required_positional`）

    **拼出来的值是【内部 id】** —— 对外契约仍是调用方传进来的**原 `thread_id`**
       （响应里回显原值）。调用方 不需要、也不应该感知这个前缀。
    """
    _require_identity(user_name, "user_name")
    _require_identity(thread_id, "thread_id")
    # 长度前缀 ⇒ 用户名到哪儿结束是写死的 ⇒ 切分点唯一 ⇒ 无歧义（见模块 docstring）
    return f"{len(user_name)}:{user_name}:{thread_id}"
