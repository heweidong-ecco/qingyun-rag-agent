"""通用按 key 断路器（`R2`）。
"""


def global_key() -> str:
    """今天的**全站**断路器 key（形如 `global:`）。

    ## 为什么要这个 helper（而不是让调用方各自拼）

    它要写在**8 个**调用点上。8 份 `f"global:{date.today.isoformat}"` =
    8 处会各自漂的日期格式，而且**漂了不报错** —— `circuit` 认不出就走 fail-open，
    **熔断静默失效、所有测试照绿**。

    日期**留在 key 里**（不是藏进 `circuit` 内部）是**故意的**：
    让"跨天"这件事对**日志**和**调用方**都是显式的 —— 出问题时一眼看得出是哪一天的额度。
    """
    from datetime import date
    return f"global:{date.today().isoformat()}"


def circuit(key: str, estimated_tokens: int = 0) -> tuple[bool, str]:
    """按 `key` 判定该不该拦。返回 **`(是否放行, 原因)`**。

    语义是「**放行**」不是「已熔断」—— 与 `check_global_daily_budget` /
    `check_session_token_budget` 保持同一约定（本仓这几层全是 `(ok, why)`），
    别再引入第二种极性。

    ## key 的形态

    * `global:<YYYY-MM-DD>` —— 全站日级（第一版只用这一个）
    * `model:<模型名>:<YYYY-MM-DD>` —— 尚未实现（用；**加一个分支即可**）

    ## 参数

    * `key` —— 见上。**含前缀与日期**，日期让"跨天"这件事对调用方显式。
    * `estimated_tokens` —— 本次预估消耗。`0` 表示**只查"现在超了没"**，不做预估拦截。

    ## 为什么是 `from ... import` 写在函数里

    与 `check_global_daily_budget` 同款：本模块被 `main.py` 与各 router 导入，
    **模块级导入容易形成环**。而且函数内导入让 `monkeypatch` 能直接打桩到源模块。
    """
    if key.startswith("global:"):
        from billing.token_tracker import check_global_daily_budget
        return check_global_daily_budget(estimated_tokens)

    # 未知前缀 ⇒ fail-open（理由见模块头）。别改成 return False。
    return True, f"未知断路器 key 前缀（{key!r}）⇒ 放行（fail-open）"
