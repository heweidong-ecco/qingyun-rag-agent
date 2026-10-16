"""**真库闸**（§2.7）—— 要真库的用例，库名必须以 `_test` 结尾。
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
    assert "前科" in msg, "要点名那条前科 —— 不然这条闸看起来像是多余的谨慎"


@pytest.mark.parametrize("bad", ["", None, "rag_db", "ragtest", "rag_test2", "test"])
def test_anything_not_ending_in_test_is_refused(bad):
    """边界：**只有 `_test` 结尾**才算隔离。`rag_test2` / `test` 都（它们不是那个库）。

    `None` / `""` 也要拦住 —— 那两种情况下**读不出库名**，
       而"读不出"绝不能当成"没事"（本仓立场：**取不到真值 ≠ 通过**）。
    """
    assert problem_of(bad), f"{bad!r} 该被拒"
