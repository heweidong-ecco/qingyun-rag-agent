"""`session_key` 的单元判据 —— 会话键必须**按人分开**（丙段）。

**背景（为什么有这个文件）**：4 张图、7 处 `{"configurable": {"thread_id": thread_id}}`
   的 key 里**都没有人**；而 `thread_id` 在 6 条端点上的**默认值就是 `"default"`**
   ⇒ 两个用户都用默认值 ⇒ **共用一个 checkpoint 桶**，而 reducer 是
   `Annotated[List, operator.add]` ⇒ **消息 append 到别人的历史上，模型看得到**。

   对照：**花费**那条轴**没有**这个问题 —— `check_session_token_budget(user_name, thread_id)`
   的 key 含 `user_name`。⇒ 同一个仓里两套口径：**花钱的账按人算，对话记忆不按人算。**

判据（可打印）：
    `venv/bin/python -m pytest app/tests/test_session_key.py -q -p no:warnings`
"""
import inspect

import pytest

from access.session_key import session_key


# ==================== ① 核心：同 thread_id + 不同人 ⇒ 必须不同 ====================

def test_same_thread_id_different_users_do_not_collide():
    """这一条就是丙段要修的那个 bug —— 两个人用同一个 `thread_id` 不能共用一个桶。"""
    assert session_key("alice", "default") != session_key("bob", "default")


def test_same_user_different_threads_do_not_collide():
    """同一个人的两个会话也得分得开（这条改之前**就成立**，是防止修①时改坏它）。"""
    assert session_key("alice", "a") != session_key("alice", "b")


def test_stable_for_same_input():
    """同样的输入必须给出同样的键 —— 否则续跑/审批会找不到原来的 checkpoint。"""
    assert session_key("alice", "default") == session_key("alice", "default")


# ==================== ② 注入性：拼接不能有歧义 ====================

def test_join_is_unambiguous():
    """朴素拼法 `f"{user}:{thread}"` **有歧义**：
    `("a", "b:c")` 与 `("a:b", "c")` 都会拼成 `"a:b:c"` ⇒ 两个不同的人共用一个桶。

    用户名由 `create_user_api_key` 创建，**没有字符校验** ⇒ 含 `:` 的用户名是可能的。
    ⇒ 拼接必须**无歧义**（长度前缀 / 转义 / 任选其一，不能是裸 `:` 连接）。
    """
    assert session_key("a", "b:c") != session_key("a:b", "c")


# ==================== ③ fail-closed：缺身份要抛错，不是"当匿名" ====================

@pytest.mark.parametrize("user, thread", [
    ("", "default"),
    ("alice", ""),
    (None, "default"),
    ("alice", None),
])
def test_missing_identity_raises(user, thread):
    """不许"缺了就退回默认" —— 那正是 fail-open（§二 根因）。
    与 `db._require_identity` / `bm25_index._require_identity` 同一条约定。"""
    with pytest.raises(ValueError):
        session_key(user, thread)


# ==================== ④ 结构性守卫：形参必须【必填】 ====================

def test_both_params_are_required_positional():
    """与 `hybrid_search(..., *, user_id)` 同一条约定：**不给默认值** ——
    有默认值 = "可以忘记传" = 还是 fail-open（漏传即 `TypeError`，不是静默串号）。"""
    sig = inspect.signature(session_key)
    for name in ("user_name", "thread_id"):
        assert name in sig.parameters, f"`session_key` 少了形参 `{name}`"
        assert sig.parameters[name].default is inspect.Parameter.empty, (
            f"`{name}` 有默认值 —— 等于「可以忘记传」，就还是 fail-open"
        )
