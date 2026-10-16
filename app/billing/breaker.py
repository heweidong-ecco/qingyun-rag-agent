"""通用按 key 断路器（`R2`）。

## 它是什么 / 不是什么

**是**：一个**派发器**。`circuit(key)` 按 key 前缀找到对应的判定器，
把"该不该拦"这个决定**委托出去**。

**不是**：一个自己判额度的东西。别在这里另写一套判定 ——
那会和 `token_tracker` 里的口径**悄悄分叉**，而且**两边都不报错**。

## 为什么不按教科书做「半开 / 探测恢复」

标准熔断器有"半开"态（放一个探针请求试上游）。**这里不需要**，理由：
本仓熔断的对象是**预算**，而预算的周期是**天** ⇒ **恢复机制天然就是"跨天自然重置"**。
做半开只会引入「预算没到却被当探针放行」的复杂度。

> 恢复的**实现**其实早就有了 —— 全站用量走的是
> `WHERE created_at >= CURRENT_DATE`（`token_tracker.get_global_daily_token_usage`），
> **跨天查询窗口自己翻页**，与 Redis TTL 无关（源文档曾标"Redis 日级 key TTL 未核"，
> 那是**对存储的误判** —— 日级用量在 PG，不在 Redis）。

## 未知 key ⇒ **fail-open**，这是有意的

额度是**成本控制**，**不是安全边界**（与 `app/routing/deps.py` 鉴权 fail-closed **方向相反**）。
⇒ 拼错的 key、或  将来加了新前缀而本文件还没跟上，
**都不该把服务打死** ⇒ 放行 + 打日志，让人看得。

## 本文件【自己】没有调用点也是白搭

接线在 `①b`：**与  同一批调用点**
（`api_v1_agent.py` 5 处 + `api_v1_rag.py` 3 处 + 匿名烧钱的 `/api/v1/rag/benchmark-embedding`）。
"""


def global_key() -> str:
    """今天的**全站**断路器 key（形如 `global:2026-10-02`）。

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
