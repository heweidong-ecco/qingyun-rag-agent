"""会话键 —— **把身份拼进 checkpoint / 会话 id**（丙段）。
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
