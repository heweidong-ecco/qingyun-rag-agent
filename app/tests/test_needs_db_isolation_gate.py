"""**真库闸**（§2.7）—— 要真库的用例，库名必须以 `_test` 结尾。

## 守的是什么

`needs_db` / `integration` 的用例会**真写库**。不带库名跑 ⇒ 写进**真库**
`token_usage_logs` 里多出**假的用量记录**，而那些记录**会进全站日 token 顶的 SUM**
（`get_global_daily_token_usage` **不带 `user_name` 过滤**）
⇒ **一条 `total_tokens = 999 999` 的假记录就能吃掉当天一大截预算**，
而**没有任何异常** —— 要到当天晚些时候全站额度突然耗尽才看得出来。

本仓前科：。

## 为什么只测那个【纯函数】

真跑那条 fixture（"起个子进程、看它红不红"）在本仓**走不通** ——
子进程一旦 import `main` 就撞 mem0 的 Qdrant **单实例锁**
（实测记录见 一条已下线的旧用例 的文件头）。
⇒ 判据只能落在**被抽出来的那个纯函数**上。

**那半边的判据是【手工跑一次】（已跑过，两个方向都验了）**：

```bash
# 不带库名 ⇒ 必须红（错误里要点名 `POSTGRES_DB`）
venv/bin/python -m pytest app/tests/test_session_budget.py -q
# 带上库名 ⇒ 闸放行（用例本身正常跑）
POSTGRES_DB=rag_test venv/bin/python -m pytest app/tests/test_session_budget.py -q
```

判据（可打印）：`venv/bin/python -m pytest app/tests/test_needs_db_isolation_gate.py -q -p no:warnings`
"""
import pytest

from conftest import needs_db_isolation_problem as problem_of


def test_a_test_database_passes():
    """`_test` 结尾 ⇒ **放行**（不是"什么都拦"）。

    这条是**正控** —— 少了它，一个"恒返回问题"的实现在下面那条测试里**也是绿的**。
    反证：把 `endswith("_test")` 改成 `endswith("_zzz")` ⇒ 本条立刻红。
    """
    for ok in ("rag_test", "rag_test_test", "x_test"):
        assert problem_of(ok) is None, f"{ok!r} 该被放行"


def test_the_real_database_is_refused_with_a_runnable_recipe():
    """**真库被拒，而且那句话里要给出【跑法】** —— 否则下一个人只会看到一句"不行"。

    反证：把函数体改成 `return None` ⇒ 本条立刻红。
    """
    msg = problem_of("rag_db")
    assert msg, "真库名该被拒 —— 放行它 = 假用量会写进去，而且**没有任何异常**"
    assert "rag_db" in msg, "要把**实际读到的库名**打出来（不然不知道它读到了什么）"
    assert "POSTGRES_DB=rag_test" in msg, "要给一条**能直接抄的**跑法"
    assert "复盘" in msg, "要点名那条前科 —— 不然这条闸看起来像是多余的谨慎"


@pytest.mark.parametrize("bad", ["", None, "rag_db", "ragtest", "rag_test2", "test"])
def test_anything_not_ending_in_test_is_refused(bad):
    """边界：**只有 `_test` 结尾**才算隔离。`rag_test2` / `test` 都（它们不是那个库）。

    `None` / `""` 也要拦住 —— 那两种情况下**读不出库名**，
       而"读不出"绝不能当成"没事"（本仓立场：**取不到真值 ≠ 通过**）。
    """
    assert problem_of(bad), f"{bad!r} 该被拒"
