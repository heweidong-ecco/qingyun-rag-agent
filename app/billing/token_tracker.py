"""
Token 统计与成本追踪模块（支持数据库持久化）
"""
import time
from typing import Optional, Dict
from dataclasses import dataclass, field
from collections import defaultdict
# 从 db.py 导入数据库连接（注意路径）
#    （L10 与 L13）。后果：`import token_tracker` 会连带拉起 **psycopg2**，
#    哪怕调用方只想用本模块的纯计算函数（PRICING / 预估 / 汇总）。
import threading
import json
from datetime import datetime, timedelta, timezone  # `_iso_utc` / `_seconds_to_tomorrow` 用（stdlib）

# ==================== 数据模型 ====================
@dataclass
class TokenUsage:
    """单次 LLM 调用的 Token 用量"""
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    purpose: str           # 调用目的：answer_generation, query_rewrite, tool_execution, etc.
    user_name: str         # 哪个用户
    thread_id: str         # 哪个会话
    cost: float
    timestamp: float = field(default_factory=time.time)

# ==================== 全局统计存储（线程安全，用于快速汇总查询） ====================
_usage_records: list[TokenUsage] = []
_lock = threading.Lock()

# 按用户汇总的统计
_user_summary: Dict[str, Dict] = defaultdict(lambda: {"total_tokens": 0, "total_cost": 0.0, "calls": 0})

# 按用途汇总的统计
_purpose_summary: Dict[str, Dict] = defaultdict(lambda: {"total_tokens": 0, "total_cost": 0.0, "calls": 0})

# 新增：按 thread_id 汇总的统计
_thread_summary: Dict[str, Dict] = defaultdict(lambda: {"total_tokens": 0, "total_cost": 0.0, "calls": 0})

# ==================== 模型计费单价（元/1000 tokens） ====================
#    这里保留 `PRICING` / `_DEFAULT_PRICING` 两个**同名别名** ⇒ 现有调用方与测试无需改动。
#    行为不变：值逐字相同。
#    为什么搬：额度常量原先散在 4 个文件 6 处，单位还混着 —— 见 `token_config.py` 顶部。
from billing.token_config import (                                    # noqa: E402
    MODEL_PRICING as PRICING,
    DEFAULT_MODEL_PRICING as _DEFAULT_PRICING,
)
#    的**一份重复定义**，与文件下方（`get_user_token_budget` 之前）那份**逐字同值**，
#    而 Python **后者胜出** ⇒ **这一份是被完全遮蔽的死定义**（改它不生效、也不报错）。
#    下方那份**还带注释**（"免费用户：每天1万token"），是更好的那份 ⇒ 保留下方、删此处。
#    与 §二（`remove_noise_markers` 重复定义）**同型**。


def record_usage(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    purpose: str,
    user_name: str = "unknown",
    thread_id: str = "unknown",
    tool_name: str = None,
    tool_args: dict = None,
):
    """记录一次 LLM 调用的 Token 消耗，同时写入内存缓存和数据库。"""
    from core.db import get_db
    total = prompt_tokens + completion_tokens
    # 计算成本（必须先于 TokenUsage 构造，否则引用未定义变量）
    cost = compute_cost(model, prompt_tokens, completion_tokens)   #  契约 B：唯一算式
    usage = TokenUsage(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
        purpose=purpose,
        user_name=user_name,
        thread_id=thread_id,
        cost=cost,
    )
    
    # 1. 写入内存缓存
    with _lock:
        _usage_records.append(usage)
        # 只保留最近 1000 条记录，防止内存溢出
        if len(_usage_records) > 1000:
            _usage_records.pop(0)
        
        # 更新用户汇总
        _user_summary[user_name]["total_tokens"] += total
        _user_summary[user_name]["total_cost"] += cost
        _user_summary[user_name]["calls"] += 1
        
        # 更新用途汇总
        _purpose_summary[purpose]["total_tokens"] += total
        _purpose_summary[purpose]["total_cost"] += cost
        _purpose_summary[purpose]["calls"] += 1

        # 新增：更新 thread 维度汇总
        _thread_summary[thread_id]["total_tokens"] += total
        _thread_summary[thread_id]["total_cost"] += cost
        _thread_summary[thread_id]["calls"] += 1

    # 2. 持久化到数据库（异步写入，不影响主流程）
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO token_usage_logs
                       (user_name, thread_id, model, purpose, prompt_tokens, completion_tokens, total_tokens, cost)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (user_name, thread_id, model, purpose, prompt_tokens, completion_tokens, total, cost)
                )
                conn.commit()
    except Exception as e:
        print(f"[Token] 持久化Token记录失败: {e}")
    # 新增：持久化写入数据库
    record_cost(
        user_name=user_name,
        thread_id=thread_id,
        model=model,
        purpose=purpose,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_name=tool_name,
        tool_args=tool_args,
    )
    print(f"[Token] {purpose} | {model} | {total} tokens | ¥{cost:.4f} | 用户: {user_name}")
    
    # 当单次调用或单日花费超过预设阈值时，自动发送告警
    DAILY_COST_ALERT_THRESHOLD = 10.0  # 每日花费超过10元时告警
    if get_user_summary(user_name).get("total_cost", 0) > DAILY_COST_ALERT_THRESHOLD:
        print(f"⚠️ 用户 {user_name} 今日花费已超过 {DAILY_COST_ALERT_THRESHOLD} 元！")


# ==================== 预算耗尽时的统一话术 ====================
#    各自要返回一句"超出预算"给用户。若各写各的，措辞会漂成四五份 ——
#    统一放这里，别在 `agent_graph*.py` 里再抄字面量。
BUDGET_EXCEEDED_MSG = "今日Token预算已用完，请明天再试。"


# ====================  契约 B：计价与取用量的公共件 ====================
# 这三个函数是 `/rag/stream_search` 的 `usage` 帧与【账本】**同源**的保证。
#    别在端点里再写一遍 `getattr(llm, "model_name", …)` 或再算一次钱 ——
#       那就是**第二份取数口径**，而  的全部代价正是从"模型名有两个来源"来的

def resolve_model_name(llm_obj) -> str:
    """从 LLM 对象取模型名 —— **唯一实现**（契约 B）。

    为什么要单独一个函数：账本（`record_from_response`）与 `usage` 帧
       （`/rag/stream_search`）读的是**同一处**。让两边各写一遍 `getattr`，
       就是造第二个可能漂的实现 —— 而本仓因"模型名有两个来源"按错单价记过两次账。

    取值顺序 `model_name` → `model` → `"unknown"` 是**照抄原实现**的，别改；
       两样都没有时返回 `"unknown"`（不是 `None`）—— 下游要拿它去查 `PRICING`。
    """
    return getattr(llm_obj, "model_name", None) or getattr(llm_obj, "model", "unknown")


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """一次调用的成本（元）—— **唯一实现**（契约 B）。

    改前同一个算式在**本文件里有两份**（`record_usage` 与 `record_cost`），
       各写各的 ⇒ 未登记模型时两张表可能对不上账（`token_config.py` 顶部记过这条约定）。
       本刀要用的钱数除了抽取**没有别的复用方式** ⇒ 抽出来三处共用，不写第三份。
    """
    pricing = PRICING.get(model, _DEFAULT_PRICING)
    return (prompt_tokens / 1000) * pricing["prompt"] + (completion_tokens / 1000) * pricing["completion"]


def usage_summary(llm_obj, response) -> Optional[dict]:
    """把一次响应的用量整理成 `usage` 帧的载荷；**没带用量 ⇒ `None`**。

    键固定为 `{"model", "prompt_tokens", "completion_tokens", "cost_usd"}`。

    判据与 `record_from_response` **同一个属性**：`usage_metadata`。
       不是 `.usage` —— `AIMessage` / `AIMessageChunk` **都没有**那个属性，
       `hasattr(response, "usage")` **恒为假**（本仓墓碑：`agent_checkpointer.py` 那整段
       记账从未执行过，而测试全绿）。

    返回 `None` 时调用方**必须不出帧** —— 「记账了才出帧」是一条判据，不是一个建议。
       它保证帧里报的钱与账本里的钱**同一时刻、同一来源**。
    """
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return None
    model = resolve_model_name(llm_obj)
    prompt_tokens = usage.get("input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    return {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": compute_cost(model, prompt_tokens, completion_tokens),
    }


def record_from_response(
    llm_obj,
    response,
    purpose: str,
    *,
    user_name: str = "unknown",
    thread_id: str = "unknown",
    tool_name: str = None,
    tool_args: dict = None,
) -> bool:
    """从一次 LLM 响应里取用量并记账 —— **唯一实现**（§四）。

    ## 为什么要有它

    三条会真调 LLM 的链（`/agent/langgraph_chat` · `/agent/advanced_chat`
    `/agent/memory_chat`）此前**一分钱不记**。修法是给 8 个调用点一个共同入口 ——
    不让每个调用点各抄一遍取用量的那几行，否则**必然漂**
    （同款立场见 `agent_graph_advanced_learning.py` 顶部"两表结构上不可能再漂"）。

    ## 判据必须是 `usage_metadata`

    `AIMessage` / `AIMessageChunk` **都没有** `.usage` 属性 —— 真名是 `usage_metadata`。
    本仓 `agent_checkpointer.py:83` 曾写 `hasattr(response, "usage")`，**恒为假**
    ⇒ 那整段记账**从未执行过**，而所有测试照样全绿。
    `app/tests/test_token_budget_hookup.py::test_does_not_record_on_the_old_wrong_attribute`
    就是它的墓碑：拿一个**只有 `.usage`** 的对象来调，本函数必须返回 `False`。

    ## 返回

    * `True` —— 真写了账（`token_usage_logs` + `cost_records` 各一行）
    * `False` —— 这次响应没带用量 ⇒ **静默跳过，不抛异常**
      （同 `get_daily_token_usage` 的 fail-open 取向：模型没回 usage 不该让整个请求 500）
      **代价要认**：这一笔会**漏记**。

    ## `model=` 从对象取，不许写死

    本仓因写死 `"qwen-turbo"` 按**错的单价**记过账
    （`agent_graph_advanced.py:352-354` · `agent_checkpointer.py:85-87` 两处前科）。
    """
    # 不用 `hasattr(response, "usage")` —— 见 docstring 与那是墓碑的测试
    summary = usage_summary(llm_obj, response)
    if summary is None:
        return False

    record_usage(
        model=summary["model"],
        prompt_tokens=summary["prompt_tokens"],
        completion_tokens=summary["completion_tokens"],
        purpose=purpose,
        user_name=user_name,
        thread_id=thread_id,
        tool_name=tool_name,
        tool_args=tool_args,
    )
    return True


def record_cost(
    user_name: str,
    thread_id: str,
    model: str,
    purpose: str,
    prompt_tokens: int,
    completion_tokens: int,
    tool_name: str = None,
    tool_args: dict = None,
):
    """
    将单次调用的花费明细写入数据库。
    这是花费数据持久化的核心函数。
    """
    from core.db import get_db
    # 计算费用
    #  契约 B：同一算式取 0 权重 ⇒ 分量与总数**结构上不可能对不上**。
    # 那两个**分量列**是 `cost_records` 的表结构要的，不能只留总数。
    input_cost = compute_cost(model, prompt_tokens, 0)
    output_cost = compute_cost(model, 0, completion_tokens)
    total_cost = input_cost + output_cost
    
    # 序列化工具参数
    tool_args_json = json.dumps(tool_args, ensure_ascii=False) if tool_args else None
    
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO cost_records
                       (user_name, thread_id, model, purpose, prompt_tokens, completion_tokens,
                        total_tokens, input_cost, output_cost, total_cost, tool_name, tool_args)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (user_name, thread_id, model, purpose, prompt_tokens, completion_tokens,
                     prompt_tokens + completion_tokens, input_cost, output_cost, total_cost,
                     tool_name, tool_args_json)
                )
                conn.commit()
    except Exception as e:
        print(f"[Cost] 持久化花费记录失败: {e}")
# ==================== 从数据库查询当日 Token 使用量（用于预算控制） ====================

def get_daily_token_usage(user_name: str) -> float:
    """
    从数据库查询用户今日已消耗的 Token 总数。
    这是预算控制的权威数据源，不依赖内存缓存。
    """
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COALESCE(SUM(total_tokens), 0)
                       FROM token_usage_logs
                       WHERE user_name = %s
                         AND created_at >= CURRENT_DATE""",
                    (user_name,)
                )
                return cur.fetchone()[0]
    except Exception as e:
        print(f"[Token] 查询当日用量失败: {e}")
        # 降级：返回内存缓存中的值
        with _lock:
            return _user_summary.get(user_name, {}).get("total_tokens", 0)

# ==================== 今日 · 按用途拆（前端整体调整 批 4） ====================

#: 算作 **LLM**（生成 / 决策 / 改写那一路）的用途。
LLM_PURPOSES = (
    "agent_decision",
    "answer_generation",
    "query_rewrite",
    "query_expand",
    "plan_execute.plan",
    "plan_execute.dynamic_input",
    "plan_execute.quality_check",
    #    它此前**根本不存在**（mem0 用自己的 LLM 且不记账）⇒ 加它 = **新开一条记账**。
    #    忘了把它加进来的话，`app/tests/test_daily_token_split.py` 会红
    #      （那条门扫活代码里每一个 `purpose="…"`，**新 purpose 必须归类**）。
    "memory_extract",
)

#: 算作 **Embedding** 的用途。
EMBEDDING_PURPOSES = ("embedding",)


def get_daily_tokens_by_purpose(user_name: str) -> dict:
    """今日**按用途**分组的 token（读库）。

    **与 `get_daily_token_usage` 是同一张表、同一个时间窗口**（`created_at >= CURRENT_DATE`）
       ⇒ 两次查询的和**应当对得上**；别另立一个窗口（那会与页头那句「今日还剩」打架）。
    查库失败 ⇒ 回 **`{}`（空）**，不是编一排 0 —— 页面据此画 `—`。
    """
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT purpose, COALESCE(SUM(total_tokens), 0)
                       FROM token_usage_logs
                       WHERE user_name = %s
                         AND created_at >= CURRENT_DATE
                       GROUP BY purpose""",
                    (user_name,)
                )
                return {(r[0] or ""): float(r[1]) for r in cur.fetchall()}
    except Exception as e:
        print(f"[Token] 查询当日按用途用量失败: {e}")
        return {}


def split_daily_tokens(by_purpose: dict) -> dict:
    """把「按用途的日用量」拆成 **LLM / Embedding / 其它** 三份（**纯函数**，可单测）。

    **「其它」要单独列出来、不许并进任何一边** —— 并进去就是编一个数
    （本仓立场：「不许印没有数据源的数」）。
    """
    llm = emb = other = 0.0
    for key, val in (by_purpose or {}).items():
        n = float(val or 0)
        if key in EMBEDDING_PURPOSES:
            emb += n
        elif key in LLM_PURPOSES:
            llm += n
        else:
            other += n
    return {
        "llm_tokens_today": round(llm, 2),
        "embedding_tokens_today": round(emb, 2),
        "other_tokens_today": round(other, 2),
    }


# ==================== 从数据库查询历史统计（用于趋势分析） ====================
def get_user_history(user_name: str, days: int = 30) -> list:
    """获取用户最近N天的每日Token消耗历史"""
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DATE(created_at) as date,
                              SUM(total_tokens) as tokens,
                              SUM(cost) as cost,
                              COUNT(*) as calls
                       FROM token_usage_logs
                       WHERE user_name = %s
                         AND created_at >= CURRENT_DATE - %s
                       GROUP BY DATE(created_at)
                       ORDER BY date DESC""",
                    (user_name, days)
                )
                rows = cur.fetchall()
                return [{"date": str(r[0]), "tokens": r[1], "cost": round(r[2], 4), "calls": r[3]} for r in rows]
    except Exception as e:
        print(f"[Token] 查询历史数据失败: {e}")
        return []

# ==================== 原有的查询函数（保留内存缓存快速查询） ====================
# 下面这三个 `get_*_summary` 读的是【进程内存】（`_user_summary` / `_purpose_summary`
#     / `_thread_summary`，见本文件 :39/:42/:45），只在 `record_usage` 里累加，
#     **从不回读数据库** ⇒ **重启即归零**。
def get_user_summary(user_name: str = None):
    """获取用户维度的汇总统计（**进程内存**，重启归零 —— 见上）"""
    with _lock:
        if user_name:
            return dict(_user_summary.get(user_name, {}))
        return dict(_user_summary)

def get_purpose_summary():
    """获取用途维度的汇总统计（**进程内存**、且**不分用户** —— 见上）"""
    with _lock:
        return dict(_purpose_summary)

def get_thread_summary(thread_id: str = None):
    """获取线程维度的汇总统计（**进程内存**，重启归零 —— 见上）"""
    with _lock:
        if thread_id:
            return dict(_thread_summary.get(thread_id, {}))
        return dict(_thread_summary)


def get_user_overview(user_name: str) -> dict:
    """某用户的**【全时】**总览：token / 花费 / 调用次数 + 按用途拆分。**读库**。

    **与上面三个 `get_*_summary` 同名不同命，别混**：

    | | `get_user_summary` 那三个 | **本函数** |
    |---|---|---|
    | 数据源 | 进程内存（`_user_summary` 等） | **`token_usage_logs` 表** |
    | 进程重启 | **归零** | **不受影响** |
    | 服务的场景 | 进程内即时告警 | **对外展示**（`/agent/cost/overview`） |

    **窗口是【全时累计】** —— 既不是"今天"、也不是"近 N 天"。
       "今天花了多少"那类问题由 `get_token_budget_info`（`R1.3` 口径）和
       `get_daily_usage_cost`（今日花费）回答。

    **按用途拆分也是【本人】的** —— `get_purpose_summary` 那份是**全站**的
       （它不收 `user_name`），两者数值本来就不该相等。

    查库失败 ⇒ 返回**全 0**（fail-open，与 `get_daily_token_usage` 同族）；
       调用方**看不出来**是"真 0"还是"查挂了" —— 与本仓其余 fail-open 同样的取舍。
    """
    empty = {"total_tokens": 0, "total_cost": 0.0, "calls": 0, "by_purpose": {}}

    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT purpose,
                              COALESCE(SUM(total_tokens), 0),
                              COALESCE(SUM(cost), 0),
                              COUNT(*)
                       FROM token_usage_logs
                       WHERE user_name = %s
                       GROUP BY purpose""",
                    (user_name,)
                )
                rows = cur.fetchall()
    except Exception as e:
        print(f"[Token] 查询用户总览失败: {e}")
        return dict(empty)

    by_purpose = {}
    total_tokens = 0
    total_cost = 0.0
    total_calls = 0
    for purpose, tokens, cost, calls in rows:
        by_purpose[purpose] = {
            "tokens": int(tokens),
            "cost": round(float(cost), 4),
            "calls": int(calls),
        }
        total_tokens += int(tokens)
        total_cost += float(cost)
        total_calls += int(calls)

    return {
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "calls": total_calls,
        "by_purpose": by_purpose,
    }

def get_recent_usage(limit: int = 20, user_name: str = None):
    """最近若干条使用记录。

    **加【可选】`user_name` —— 给了就只看这个人。**

    **改前是什么样**：端点 `GET /agent/token/recent` 的签名**收了 `user_name`**
       （`Depends(get_current_user_hybrid)`），而函数体写的是 `get_recent_usage(limit + 1)`
       ⇒ **身份根本没往下传** ⇒ 它返回的是 `_usage_records` 里**所有人的**记录，
       **每条还带着别人的 `user_name`**。
       ⇒ 那是  /  那一族的同型（**身份收了不用 ⇒ 静默查全库**）；
          在公开 线上把这条接口露出去 = **越权**（§1.2）。

    **顺序要紧**：**先按人过滤，再取最后 `limit` 条** ——
       反过来（先切片再过滤）会在"总记录很多、这个人占得少"时**回空**，
       而那看起来像"他没有记录"，**不报任何错**。
    """
    with _lock:
        rows = _usage_records
        if user_name:
            rows = [u for u in rows if u.user_name == user_name]
        return [
            {
                "model": u.model,
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens,
                "purpose": u.purpose,
                "user_name": u.user_name,
                "thread_id": u.thread_id,
                "cost": round(u.cost, 4),
                "timestamp": u.timestamp,
            }
            for u in rows[-limit:]
        ]

# ==================== 预算控制 ====================
# ==================== 预算控制函数（已改用数据库查询） ====================
# 1. 成本计算与预算控制

def get_user_token_budget(user_name: str) -> float:
    """该用户**一天的总上限**（token）。

    **（两个额度池）语义变过** —— 改前它 = `ROLE_DAILY_TOKEN[角色]`；
    现在 = **两个池【相加】**（`quota_pools.total_daily_limit(角色)`）。

    **为什么非改不可**：日额度拆成两个池之后，**总上限是翻倍的**（作者 确认）。
    而**本函数是"总额"那道闸的唯一来源** —— `check_token_budget_detail` 用它，
    全仓 **11 处图内闸**（`agent_graph*.py` / `agent_checkpointer.py` / `plan_execute.py`）共用它
    ⇒ 不改它，那些闸仍然在 **1× 角色额度**处把请求拦住 ⇒ **"翻倍"根本不会发生**，
    而页头显示的却是 2× ⇒ **两个数对不上**（本仓最忌的「看着修好了」）。

    因此凡是引用它的地方（`check_budget_warning` · `get_token_budget_info`
    `cost_dashboard` · `/debug/quota`），**显示的数也跟着翻倍** —— **这是有意的**。
    逐条裁定
    """
    from access.permission import get_user_role
    from billing.quota_pools import total_daily_limit

    role = get_user_role(user_name)
    return total_daily_limit(role)


def get_pool_budget_info(user_name: str) -> dict:
    """**按池**的「额度 / 今日已用 / 剩余」—— 页头与 `QuotaMiddleware` 共用这一份口径。

    **「已用」是按 `purpose` 分的**（`split_daily_tokens`），而**归池是按【路径】的**
    —— 两者**不总是一致**（一条"两边都动"的路由按路径进 LLM 池，但它产生的 embedding 用量
    记在 embedding 那一栏）。**那是有意的近似，不是 bug** ——
    详见 `app/billing/quota_pools.py` 模块头那段「两件事别读混」。
    **同一个时间窗口**：`created_at >= CURRENT_DATE`（与 `get_daily_token_usage` 同源）
    ⇒ 三个 `used` 与页头那句应当对得上；别另立一个窗口。
    查库失败 ⇒ `split` 回 `{}`（三个数都是 0.0）—— 与 `get_daily_token_usage` 的
    fail-open 取向一致，不在这里加特判。
    """
    from access.permission import get_user_role
    from billing.quota_pools import (
        POOL_LLM, POOL_EMBEDDING, pool_daily_limit, total_daily_limit,
    )

    role = get_user_role(user_name)
    split = split_daily_tokens(get_daily_tokens_by_purpose(user_name))

    def _one(limit: float, used: float) -> dict:
        return {"limit": limit, "used": used, "remaining": max(0.0, limit - used)}

    llm_limit = pool_daily_limit(POOL_LLM, role)
    emb_limit = pool_daily_limit(POOL_EMBEDDING, role)
    total_used = get_daily_token_usage(user_name)      # == 全部 purpose 之和（含 other）

    return {
        "role": role,
        POOL_LLM: _one(llm_limit, split["llm_tokens_today"]),
        POOL_EMBEDDING: _one(emb_limit, split["embedding_tokens_today"]),
        # 总额用 `get_daily_token_usage`（**含 other**），不是"两池相加"
        #    —— 后者会把 `other` 那一栏漏掉，而 `check_token_budget_detail` 比的就是前者。
        "total": _one(total_daily_limit(role), total_used),
    }

def check_token_budget_detail(user_name: str, estimated_tokens: int = 0) -> tuple[bool, str]:
    """每日 Token 预算检查 —— **唯一实现**，带原因说明。

    抽出本函数：此前 `check_token_budget` / `check_budget_before_call` /
    `check_multilevel_budget`(第三级) **各写一份**同样的判定，且后两份把单位写成「元」
    去和 **Token 预算**相减 ⇒ **恒放行**）。
    本函数是**量纲正确**的那一份（Token vs Token），其余一律委托到这里。

    **不含** `record_intercept` —— 拦截记录由调用方负责（各调用方要带上自己的 tool_name）。
    """
    budget = get_user_token_budget(user_name)
    if budget == float("inf"):
        return True, "管理员无限预算"
    used = get_daily_token_usage(user_name)          # Token（权威数据源，不依赖内存缓存）
    remaining = budget - used
    if remaining <= 0:
        return False, f"今日预算已用完（已使用 {used:.0f} tokens，预算 {budget:.0f} tokens）"
    if estimated_tokens > 0 and estimated_tokens > remaining:
        return False, (f"预估消耗 {estimated_tokens:.0f} tokens 超过剩余预算 "
                       f"{remaining:.0f} tokens")
    return True, f"预算充足（剩余 {remaining:.0f} tokens，预估 {estimated_tokens:.0f} tokens）"


def check_token_budget(user_name: str, estimated_tokens: int = 0) -> bool:
    """
    检查用户是否还有Token预算。

    参数:
        user_name: 用户名
        estimated_tokens: 本次调用预估消耗的Token数（可选）

    返回:
        True: 预算充足，可以调用
        False: 预算已用完
    """
    return check_token_budget_detail(user_name, estimated_tokens)[0]

def get_token_budget_info(user_name: str) -> dict:
    """
    获取用户的Token预算信息（用于返回给客户端）。
    """
    budget = get_user_token_budget(user_name)
    used = get_daily_token_usage(user_name)
    remaining = max(0, budget - used) if budget != float("inf") else -1
    
    return {
        "daily_budget": budget if budget != float("inf") else "无限",
        "used_today": round(used, 2),
        "remaining": round(remaining, 2) if remaining != -1 else "无限",
    }


# ====================  新增月度报告生成函数 ====================
def generate_monthly_report(user_name: str, year: int = None, month: int = None) -> dict:
    """
    生成用户指定月份的花费报告。
    
    参数:
        user_name: 用户名
        year: 年份（默认当前年）
        month: 月份（默认当前月，1-12）
    
    返回:
        包含总花费、日均花费、用途分布等信息的报告字典
    """
    from datetime import datetime
    from core.db import get_db

    # 默认当前月份
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month
    
    # 计算月份范围
    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{year + 1}-01-01"
    else:
        end_date = f"{year}-{month + 1:02d}-01"
    
    # 计算该月天数
    import calendar
    days_in_month = calendar.monthrange(year, month)[1]
    elapsed_days = min(now.day, days_in_month) if year == now.year and month == now.month else days_in_month
    
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 1. 总览统计
                cur.execute(
                    """SELECT
                          COALESCE(SUM(total_tokens), 0) as total_tokens,
                          COALESCE(SUM(cost), 0) as total_cost,
                          COUNT(*) as total_calls
                       FROM token_usage_logs
                       WHERE user_name = %s
                         AND created_at >= %s
                         AND created_at < %s""",
                    (user_name, start_date, end_date)
                )
                row = cur.fetchone()
                total_tokens = row[0]
                total_cost = round(row[1], 4)
                total_calls = row[2]
                
                # 2. 按用途分布统计
                cur.execute(
                    """SELECT purpose,
                              SUM(total_tokens) as tokens,
                              SUM(cost) as cost,
                              COUNT(*) as calls
                       FROM token_usage_logs
                       WHERE user_name = %s
                         AND created_at >= %s
                         AND created_at < %s
                       GROUP BY purpose
                       ORDER BY cost DESC""",
                    (user_name, start_date, end_date)
                )
                purpose_rows = cur.fetchall()
                
                # 3. 按模型分布统计
                cur.execute(
                    """SELECT model,
                              SUM(total_tokens) as tokens,
                              SUM(cost) as cost,
                              COUNT(*) as calls
                       FROM token_usage_logs
                       WHERE user_name = %s
                         AND created_at >= %s
                         AND created_at < %s
                       GROUP BY model
                       ORDER BY cost DESC""",
                    (user_name, start_date, end_date)
                )
                model_rows = cur.fetchall()
                
    except Exception as e:
        #    这个 `{"error": str(e)}` **就是** `GET /agent/monthly_report` 那条告警的**源头** ——
        #    report 被端点原样返回，异常原文（SQL 片段 / 连接串 / 路径）就跟着出去。
        #    **日志这一行【一个字没改】**（细节本来就该留在服务端），
        #    只把**返回给调用方**的那份换成**不带内部细节**的可读原因。
        print(f"[Token] 生成月度报告失败: {e}")
        return {"error": "生成月度报告失败，请稍后重试（详细信息见服务端日志）"}
    
    # 构建报告
    return {
        "report_period": f"{year}年{month}月",
        "days_in_month": days_in_month,
        "elapsed_days": elapsed_days,
        "summary": {
            "total_cost": total_cost,
            "total_tokens": total_tokens,
            "total_calls": total_calls,
            "daily_average_cost": round(total_cost / elapsed_days, 4) if elapsed_days > 0 else 0,
            "daily_average_tokens": round(total_tokens / elapsed_days, 0) if elapsed_days > 0 else 0,
            "cost_per_call": round(total_cost / total_calls, 4) if total_calls > 0 else 0,
        },
        "by_purpose": [
            {
                "purpose": r[0],
                "tokens": r[1],
                "cost": round(r[2], 4),
                "calls": r[3],
                "percentage": round(r[2] / total_cost * 100, 1) if total_cost > 0 else 0
            }
            for r in purpose_rows
        ],
        "by_model": [
            {
                "model": r[0],
                "tokens": r[1],
                "cost": round(r[2], 4),
                "calls": r[3],
                "percentage": round(r[2] / total_cost * 100, 1) if total_cost > 0 else 0
            }
            for r in model_rows
        ],
    }


# ==================== 工具调用预估成本 ====================

# 不同工具的单次调用预估成本（元）
TOOL_ESTIMATED_COST = {
    "web_search": 0.005,         # 搜索工具：通常需要一次 LLM 辅助总结
    "calculator": 0.0,           # 计算器：本地执行，无 API 调用
    "date_today": 0.0,           # 日期查询：本地执行
    "date_calc": 0.0,            #：本地纯函数，无 API 调用
    "json_extract": 0.0,         #：本地纯函数
    "stats": 0.0,                #：本地纯函数
    "fetch_webpage": 0.003,      # 网页抓取：可能触发 LLM 总结
    "screenshot_webpage": 0.003, # 截图：类似网页抓取
    "execute_python": 0.0,       # 代码执行：本地执行，无 API 调用
}

# 不同用途的单次调用预估成本（元）
PURPOSE_ESTIMATED_COST = {
    "agent_decision": 0.01,      # Agent 决策节点
    "answer_generation": 0.02,   # 生成最终答案
    "query_rewrite": 0.005,      # 查询改写
    "embedding": 0.0005,         # Embedding 调用
    #    （两套表并存不可互相替代 —— 见下面那段说明）。
    "plan_execute.plan": 0.01,
    "plan_execute.dynamic_input": 0.005,
    "plan_execute.quality_check": 0.003,
}

def estimate_tool_cost(tool_name: str) -> float:
    """预估单次工具调用的花费（**元** —— 仅供展示，预算判定请用 estimate_tool_tokens）"""
    return TOOL_ESTIMATED_COST.get(tool_name, 0.01)

def estimate_purpose_cost(purpose: str) -> float:
    """预估单次 LLM 调用的花费（**元** —— 仅供展示，预算判定请用 estimate_purpose_tokens）"""
    return PURPOSE_ESTIMATED_COST.get(purpose, 0.01)

# 两套预估表**必须并存、不可互相替代**：
#    · *_COST   → 单位「元」，供 `/agent/budget/estimates` 等**对外展示**用（api_v1_agent.py 暴露了它）
#    · *_TOKENS → 单位「Token」，供**预算判定**用
#    预算的权威单位是 **Token**（ROLE_TOKEN_BUDGET / get_daily_token_usage 都是 Token）。
TOOL_ESTIMATED_TOKENS = {
    "web_search": 800,           # 搜索工具：通常需要一次 LLM 辅助总结
    "calculator": 0,             # 计算器：本地执行，无 API 调用
    "date_today": 0,             # 日期查询：本地执行
    "date_calc": 0,              #：本地纯函数，无 API 调用
    "json_extract": 0,           #：本地纯函数
    "stats": 0,                  #：本地纯函数
    "fetch_webpage": 600,        # 网页抓取：可能触发 LLM 总结
    "screenshot_webpage": 600,   # 截图：类似网页抓取
    "execute_python": 0,         # 代码执行：本地执行，无 API 调用
}

# 不同用途的单次调用预估 Token 消耗
PURPOSE_ESTIMATED_TOKENS = {
    "agent_decision": 500,       # 对齐 agent_graph_advanced.py:302/340 已在用的经验值
    "answer_generation": 800,
    "query_rewrite": 300,
    "embedding": 100,
    #    此前 `plan_execute` **完全不查预算、不记账** —— 它会真跑 LLM，而账上一行不记
    #    ⇒ **免费用户的每日配额管不到它**（`/agent/plan_execute`）。
    "plan_execute.plan": 500,           # 把目标分解成步骤清单
    "plan_execute.dynamic_input": 300,  # 每步：生成该工具那个字段的值
    "plan_execute.quality_check": 200,  # 每步：判定结果是否达标
}

# 未知工具/用途时的保守兜底（Token）
DEFAULT_ESTIMATED_TOKENS = 500

def estimate_tool_tokens(tool_name: str) -> int:
    """预估单次工具调用的 **Token** 消耗（预算判定的单位）"""
    return TOOL_ESTIMATED_TOKENS.get(tool_name, DEFAULT_ESTIMATED_TOKENS)

def estimate_purpose_tokens(purpose: str) -> int:
    """预估单次 LLM 调用的 **Token** 消耗（预算判定的单位）"""
    return PURPOSE_ESTIMATED_TOKENS.get(purpose, DEFAULT_ESTIMATED_TOKENS)

def check_budget_before_call(
    user_name: str,
    tool_name: str = None,
    purpose: str = None,
    estimated_tokens: int = None
) -> tuple[bool, str]:
    """
    在调用前检查预算是否充足。
    返回: (是否允许, 原因说明)

    参数单位是 **Token**（自；旧名 `estimated_cost` 单位是「元」）。
    那个旧参数正是本函数此前**恒放行**的原因之一 —— 它把「元」和 **Token 预算**相减。
    """
    # 1. 计算预估 Token（不再是「元」）
    if estimated_tokens is not None:
        est = estimated_tokens
    elif tool_name:
        est = estimate_tool_tokens(tool_name)
    elif purpose:
        est = estimate_purpose_tokens(purpose)
    else:
        est = DEFAULT_ESTIMATED_TOKENS

    # 2. 每日预算检查 —— 委托给**统一实现**（原先这里自己写了一份错的）
    allowed, reason = check_token_budget_detail(user_name, est)
    if not allowed:
        record_intercept(user_name, tool_name or purpose or "unknown", reason)
    return allowed, reason


def get_daily_usage_cost(user_name: str) -> float:
    """从数据库查询用户今日已消耗的总花费"""
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COALESCE(SUM(cost), 0)
                       FROM token_usage_logs
                       WHERE user_name = %s
                         AND created_at >= CURRENT_DATE""",
                    (user_name,)
                )
                return cur.fetchone()[0]
    except Exception as e:
        print(f"[Token] 查询当日花费失败: {e}")
        return 0.0
    
# ==================== 拦截统计 ====================

# 拦截计数器（按用户）
_intercept_count: Dict[str, int] = defaultdict(int)
_intercept_lock = threading.Lock()

def record_intercept(user_name: str, tool_name: str, reason: str):
    """记录一次预算拦截"""
    from core.db import get_db
    with _intercept_lock:
        _intercept_count[user_name] += 1
    # 可选：写入数据库
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS budget_intercepts (
                        id SERIAL PRIMARY KEY,
                        user_name TEXT NOT NULL,
                        tool_name TEXT,
                        reason TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                cur.execute(
                    "INSERT INTO budget_intercepts (user_name, tool_name, reason) VALUES (%s, %s, %s)",
                    (user_name, tool_name, reason)
                )
                conn.commit()
    except Exception as e:
        print(f"[Budget] 记录拦截失败: {e}")

def get_intercept_count(user_name: str = None) -> dict:
    """获取拦截统计"""
    with _intercept_lock:
        if user_name:
            return {"user_name": user_name, "total_intercepts": _intercept_count.get(user_name, 0)}
        return {
            "total_intercepts": sum(_intercept_count.values()),
            "by_user": dict(_intercept_count)
        }
    
# ==================== 多级预算配置 ====================

# 单次调用最大花费（元）· 单线程最大花费（元）
#    行为不变（默认仍 0.5 / 5.0）。
from billing.token_config import MAX_SINGLE_CALL_COST, MAX_THREAD_COST       # noqa: E402,F811

def check_multilevel_budget(
    user_name: str,
    thread_id: str = "unknown",
    tool_name: str = None,
    purpose: str = None,
    estimated_cost: float = None
) -> tuple[bool, str]:
    """
    多级预算检查：
    1. 单次调用上限（单位：**元**）
    2. 单线程上限（单位：**元**）
    3. 每日预算上限（单位：**Token**）

    本函数**刻意有两个单位**，别"统一"掉：
      · 第一、二级是**单次/单线程的花费上限**，配 `MAX_SINGLE_CALL_COST` / `MAX_THREAD_COST`（元）——
        它们量纲本来就是对的（元 vs 元），的修复**没动它们**。
      · 第三级是**每日预算**，权威单位是 **Token**（`ROLE_TOKEN_BUDGET` / `get_daily_token_usage`），
        原先误用「元」去相减 ⇒ 恒放行。现已委托给 `check_token_budget_detail`。
    """
    # 第一、二级用「元」
    if estimated_cost is not None:
        cost = estimated_cost
    elif tool_name:
        cost = estimate_tool_cost(tool_name)
    elif purpose:
        cost = estimate_purpose_cost(purpose)
    else:
        cost = 0.01

    # 第三级用「Token」—— 两个单位分开算，不混
    est_tokens = (estimate_tool_tokens(tool_name) if tool_name
                  else estimate_purpose_tokens(purpose) if purpose
                  else DEFAULT_ESTIMATED_TOKENS)
    
    # 第一级：单次调用上限
    if cost > MAX_SINGLE_CALL_COST:
        record_intercept(user_name, tool_name or "unknown", f"单次预估花费 ¥{cost:.4f} 超过上限 ¥{MAX_SINGLE_CALL_COST:.4f}")
        return False, f"单次调用预估花费 ¥{cost:.4f} 超过上限 ¥{MAX_SINGLE_CALL_COST:.4f}"
    
    # 第二级：单线程上限
    thread_cost = get_thread_cost(thread_id)
    if thread_cost + cost > MAX_THREAD_COST:
        record_intercept(user_name, tool_name or "unknown", f"线程累计花费 ¥{thread_cost:.4f} + 预估 ¥{cost:.4f} 超过线程上限 ¥{MAX_THREAD_COST:.4f}")
        return False, f"当前线程已花费 ¥{thread_cost:.4f}，预估 ¥{cost:.4f}，超过线程上限 ¥{MAX_THREAD_COST:.4f}"
    
    # 第三级：每日 Token 预算 —— 委托给**统一实现**（原先这里自己写了一份、且单位是错的）
    allowed, reason = check_token_budget_detail(user_name, est_tokens)
    if not allowed:
        record_intercept(user_name, tool_name or purpose or "unknown", reason)
    return allowed, reason

def get_thread_cost(thread_id: str) -> float:
    """查询指定线程的累计花费"""
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COALESCE(SUM(cost), 0)
                       FROM token_usage_logs
                       WHERE thread_id = %s""",
                    (thread_id,)
                )
                return cur.fetchone()[0]
    except Exception as e:
        print(f"[Budget] 查询线程花费失败: {e}")
        return 0.0


# ==================== Trace 页 · 成本轴明细（F2） ====================

# 单条线程最多回多少笔明细。有它就【必须】有 `total`（整条线程聚合）——
# 否则"总花费"会变成"最近 N 笔之和"，**小而无人察觉**。
_BREAKDOWN_LIMIT = 2000


def _iso_utc(dt) -> Optional[str]:
    """把库里取出的时间戳转成**带显式时区**的 ISO-8601 串（`…+00:00`）。

    **为什么必须显式标区**：`token_usage_logs.created_at` 的类型是 `TIMESTAMP`
       （**无时区**），而 PG 容器实测是 `Etc/UTC`（`SHOW timezone`）⇒ 存进去的是**裸的 UTC**。
       若原样 `.isoformat` 回给前端，JS 的 `new Date("T13:14:21")`
       会**当成浏览器本地时间**解析 ⇒ 东八区用户看到的时刻**静默早 8 小时**。
       （同族前科：`logs/api_*.log 混了两套时区`。）

    **别照抄 `approval_audit` 的 `.isoformat`** —— 那张表的列是 `TIMESTAMPTZ`，**天生带区**，
       两者不同源。表结构一旦从 `TIMESTAMP` 改成 `TIMESTAMPTZ`，本函数该跟着删。
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        #    `str`，不是 `datetime`** —— 那一列的类型是 `TIMESTAMP`，而 SQLite 是**动态类型**，
        #    `CURRENT_TIMESTAMP` 存进去的就是**一串文本** ⇒ 对着 str 调 `.replace(tzinfo=…)`
        #    会当场 `TypeError: str.replace() takes no keyword arguments`（**实测撞到过**）。
        # ⚠️ **⛔ 不能图省事 `return dt`** —— 那个串**不带时区**，前端
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None      # 真解不出来就回 None（⛔ 别把原串当"处理过了"返回）
    return dt.replace(tzinfo=timezone.utc).isoformat()


def thread_cost_breakdown(
    user_name: str,
    thread_id: str,
    *,
    include_all: bool = False,
) -> dict:
    """某用户在某线程上的**逐笔**花费明细（成本轴）。**查表**，不读内存。

    参数
    ----
    - `user_name` —— **必填，不给默认值**。理由同 `get_session_token_usage`：
      给了默认值，漏传的调用点不会报错，会**静默**落进一个人人共用的桶。
    - `include_all` —— **admin 例外**用。`True` 时把 `user_name` 条件换成 `1 = 1`。
      **它走的是同一条 SQL，只是换个条件**（别为此再写第二条 SQL —— 两份会漂）。
      **它【不是】"跳过归属校验"的开关** —— 谁有资格传 `True` 由**端点**判
      （`get_user_role(user_name) == UserRole.ADMIN`，显式一行）。

    返回
    ----
    ``{"items": [...], "total": {...}, "truncated": bool}``

    - `items` —— **按发生时间倒序**（最新在前），每项：
      ``{purpose, model, prompt_tokens, completion_tokens, total_tokens, cost, created_at}``
    - `total` —— **对【整条线程】聚合**（不是对 `items` 求和）：
      ``{count, total_tokens, total_cost}``
      **这就是为什么它必须由 SQL 算**：`items` 有 `LIMIT`，
      若让页面自己把 `items` 加起来，"总花费"会在笔数多时**静默变小**，而且不报错。
      ⇒ **页面显示 `total`，页面【不自己求和】**（求和只有这一份实现）。
    - `truncated` —— `total.count > len(items)` ⇒ 列表被截断（页面必须**说出来**）
    - `created_at` 是 `_iso_utc` 处理过的**带区**串

    失败时 **fail-open**：打一行日志，回**空 items + 零 total**，不抛
    （同 `get_thread_cost` / `record_intercept` 的风格 —— 查账失败不该把页面打成 500）。
    """
    user_name = user_name or "unknown"
    thread_id = thread_id or "unknown"
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 条件句与它的参数**必须在同一个分支里一起定** ——
                #    分开各写一个三元表达式，改了一处忘了另一处 ⇒ 参数个数对不上 ⇒ 运行期报错。
                #    （这不是假想的：本函数第一次写成两行三元式，靠一次变异实验才照出来。）
                # 下面这个字面量是**常量二选一**，不含任何用户输入 —— 不是 SQL 注入面。
                if include_all:
                    owner_clause, owner_params = "1 = 1", ()
                else:
                    owner_clause, owner_params = "user_name = %s", (user_name,)

                cur.execute(
                    f"""SELECT purpose, model, prompt_tokens, completion_tokens,
                               total_tokens, cost, created_at
                          FROM token_usage_logs
                         WHERE {owner_clause}
                           AND thread_id = %s
                         ORDER BY created_at DESC, id DESC
                         LIMIT %s""",
                    (*owner_params, thread_id, _BREAKDOWN_LIMIT),
                )
                rows = cur.fetchall()

                # 合计走【整条线程】—— 不受上面的 LIMIT 影响
                cur.execute(
                    f"""SELECT COUNT(*),
                               COALESCE(SUM(total_tokens), 0),
                               COALESCE(SUM(cost), 0)
                          FROM token_usage_logs
                         WHERE {owner_clause}
                           AND thread_id = %s""",
                    (*owner_params, thread_id),
                )
                count, sum_tokens, sum_cost = cur.fetchone()

        items = [
            {
                "purpose": r[0],
                "model": r[1],
                "prompt_tokens": r[2],
                "completion_tokens": r[3],
                "total_tokens": r[4],
                "cost": r[5],
                "created_at": _iso_utc(r[6]),
            }
            for r in rows
        ]
        return {
            "items": items,
            "total": {
                "count": int(count or 0),
                "total_tokens": int(sum_tokens or 0),
                "total_cost": float(sum_cost or 0.0),
            },
            "truncated": int(count or 0) > len(items),
        }
    except Exception as e:
        print(f"[Trace] 查询线程成本明细失败: {e}")
        return {
            "items": [],
            "total": {"count": 0, "total_tokens": 0, "total_cost": 0.0},
            "truncated": False,
        }


# ==================== B8 · 会话级 token 上限（①b） ====================
# 口径与接线范围。

def get_session_token_usage(user_name: str, thread_id: str) -> float:
    """某用户某会话【今日】累计 token —— **查表**，不读内存。

    **为什么不复用 `_thread_summary`**：那是**进程内存**（`record_usage` 里累加），
       **重启即清零** ⇒ 拿它当上限等于「**重启一下就能绕开限额**」。
       权威数据源只能是 `token_usage_logs`（`get_daily_token_usage` 也是这么做的）。

    **窗口 = 会话 × 今日**（决策一）——
       与仓里其余所有 token 预算（用户**日**预算）**同一口径**。
       删掉 `CURRENT_DATE` 就是**换方案**（备选一·乙「纯会话累计」），那条会让会话桶
       **被永久封死**且用户**无自救手段** ⇒ 要改先改 DEC。

    **key = `user_name` + `thread_id`**（决策二）——**`user_name` 不给默认值**。
       现有 4 个 Agent 端点的 `thread_id` **默认值是 `"default"`**；只按 `thread_id` 分桶的话，
       所有没显式传它的调用者会**共用同一个桶** ⇒ 谁先烧完 50000，**其他默认用户一起撞 429**。
       ⇒ 漏传 `user_name` 的调用点应当**立刻报错**（`TypeError`），不是静默落进 `unknown`。

    **空 key 归一到 `"unknown"`** —— 与 `record_usage` 的默认值一致。
       ⇒ 记账桶与判定桶**始终一致**；若不归一，记账进 `unknown`、判定查 `''`
       ⇒ **判定永远看不到用量**（静默放行）。
    """
    user_name = user_name or "unknown"
    thread_id = thread_id or "unknown"
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COALESCE(SUM(total_tokens), 0)
                       FROM token_usage_logs
                       WHERE user_name = %s
                         AND thread_id = %s
                         AND created_at >= CURRENT_DATE""",
                    (user_name, thread_id)
                )
                return cur.fetchone()[0]
    except Exception as e:
        print(f"[Token] 查询会话用量失败: {e}")
        # **fail-open**（返回 0 = 没超）—— 与 `get_daily_token_usage` 的取向一致。
        #    理由：配额是**成本控制**，不是**安全边界**；DB 抖动时把服务全停掉代价更大。
        #    与 `app/routing/deps.py` 的鉴权**故意相反**（那里 fail-closed，因为**那是**安全边界）——
        #       别"统一"掉。
        return 0.0


def check_session_token_budget(user_name: str, thread_id: str,
                               estimated_tokens: int = 0) -> tuple[bool, str]:
    """会话级预算判定。返回 `(是否放行, 原因)`。

    **不含** `record_intercept` —— 拦截记录由调用方负责（各调用方要带上自己的 tool_name），
    与 `check_token_budget_detail` 同一条约定。

    触顶动作 = **直接拒绝**（要素②，作者已裁）⇒ 调用方抛
    `AppException(ErrorCode.QUOTA_EXCEEDED, why)`。
    """
    from billing.token_config import SESSION_TOKEN_LIMIT
    used = get_session_token_usage(user_name, thread_id)
    remaining = SESSION_TOKEN_LIMIT - used
    if remaining <= 0:
        return False, (f"本会话预算已用完（已使用 {used:.0f} tokens，"
                       f"会话上限 {SESSION_TOKEN_LIMIT:.0f} tokens）")
    if estimated_tokens > 0 and estimated_tokens > remaining:
        return False, (f"预估消耗 {estimated_tokens:.0f} tokens 超过本会话剩余 "
                       f"{remaining:.0f} tokens")
    return True, f"会话预算充足（剩余 {remaining:.0f} tokens）"


# ==================== B10 · 全局日级 token 总额（①b） ====================

def get_global_daily_token_usage() -> float:
    """今日【全站所有用户合计】的 token —— **不带任何 `user_name` 过滤**。

    ## 为什么它必须是个【独立函数】，而不是给 `get_daily_token_usage` 加个开关

    「全局」的全部含义就是**没有 `WHERE user_name`**。做成参数（`user_name=None`）
    的话，**漏传**就会静默退化成"某个用户"，且返回值和报错**都正常** ——
    和  那个「`thread_id` 默认值是 `"default"`」是**同一类**故障：
    它不会红，只会**偏小**。⇒ 拆成两个函数，让 `test_global_daily_budget_offline.py`
    能**静态**钉住「这条 SQL 里不许有 `user_name`」。

    **为什么单独要这一层**：`admin` 的个人日上限现在也是有限值，
       **但那是另一个东西** —— **per-user 检查永远看不到「大家加起来超了」**。

    **含 admin、含所有角色** —— 按 `SUM(所有行)` 算，**不是**"按角色上限求和"。
       （`后端补齐清单`  我方建议④：否则 admin 一个人能吃完全局额度而熔断不响。）

    ``未接之前，本函数**没有调用点** —— **别读成"全局限额已生效"**。
    """
    from core.db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COALESCE(SUM(total_tokens), 0)
                       FROM token_usage_logs
                       WHERE created_at >= CURRENT_DATE"""
                )
                return cur.fetchone()[0]
    except Exception as e:
        print(f"[Token] 查询全局当日用量失败: {e}")
        # fail-open：与 B8 同一取舍，但**代价更大**（这里是"全站"）——
        # 见 check_global_daily_budget 的说明。
        return 0.0


def check_global_daily_budget(estimated_tokens: int = 0) -> tuple[bool, str]:
    """全站日级预算判定。返回 `(是否放行, 原因)`。

    与 `check_session_token_budget` / `check_token_budget_detail` **同构**：
    **不含** `record_intercept`（拦截记录由调用方负责，它才知道自己的 tool_name）。

    ## fail-open，**这是有意的** —— 并且这里比  更要紧

    `get_global_daily_token_usage` 查库失败 ⇒ 返回 0.0 ⇒ 本函数**放行**。
    额度是**成本控制**，**不是安全边界** ⇒ PG 抖一下不该把服务打死。
    但"全站"这个维度让 fail-closed 的代价**远大于**会话级：
       fail-closed 的话，**一次 DB 抖动 = 所有人的请求全 429**。
    与 `app/routing/deps.py` 的鉴权（fail-closed）**方向相反是有意的**，别"顺手统一"。

    触顶动作 = **直接拒绝**（要素②，作者 已裁）
    ⇒ 调用方抛 `AppException(ErrorCode.QUOTA_EXCEEDED, why)`。

    **本函数目前【没有调用点】** —— 接线是 ``。
    """
    from billing.token_config import GLOBAL_DAILY_TOKEN_LIMIT
    used = get_global_daily_token_usage()
    remaining = GLOBAL_DAILY_TOKEN_LIMIT - used
    if remaining <= 0:
        return False, (f"今日全站额度已用完（已使用 {used:.0f} / "
                       f"上限 {GLOBAL_DAILY_TOKEN_LIMIT:.0f} tokens），请明日再试")
    if estimated_tokens > 0 and estimated_tokens > remaining:
        return False, (f"预估消耗 {estimated_tokens:.0f} tokens 超过全站今日剩余 "
                       f"{remaining:.0f} tokens")
    return True, f"全站预算充足（剩余 {remaining:.0f} tokens）"


# ============================================================================
# 🔴 全站【embedding】的滚动窗口（§二·8 · 作者裁「甲」）
# ============================================================================
def get_global_embedding_usage(hours: int = 0) -> float:
    """全站（**不带任何 `user_name` 过滤**）最近 N 小时的 **embedding** token 合计。

    `hours=0` ⇒ 走**今日**（`CURRENT_DATE`，与别处同一个"日"口径）；
    `hours>0` ⇒ 走**滚动窗口**（`NOW() - INTERVAL 'N hour'`）。

    🔴 **它和 `get_global_daily_token_usage` 是【两条】，⛔ 别合并成"加个开关"** ——
       理由与那条**逐字同款**（见它的 docstring）：漏传参数会**静默**退化成另一个口径，
       而**返回值与报错都正常**。这里还多一层：那条数的是**全部 purpose**，
       这条**只数 `purpose = 'embedding'`** —— 因为约束它的额度是**另一把尺子**
       （DashScope 那把 key 的免费额度，按 embedding token 计）。

    ⚠️ 那个 `INTERVAL` 字面量是**用 Python 拼出来的**（`int(hours)` 强转 ⇒ 无注入面）：
       参数化替不了**表达式**，而本仓的卡口认得 `NOW() - INTERVAL '<字面量>'` 这一种形态
       （SQLite 那边它会被翻成 `datetime('now','-N hour')`）。
    """
    from core.db import get_db
    since = "CURRENT_DATE" if hours <= 0 else f"NOW() - INTERVAL '{int(hours)} hour'"
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(SUM(total_tokens), 0) FROM token_usage_logs "
                    "WHERE purpose = %s AND created_at >= " + since,
                    ("embedding",),
                )
                return cur.fetchone()[0]
    except Exception as e:
        print(f"[Token] 查询全站 embedding 用量失败: {e}")
        # fail-open（与 B8 / 全站日级同一个取舍）：额度是**成本控制**，不是安全边界
        #   ⇒ PG 抖一下不该把**全站**打死。
        return 0.0


def check_global_embedding_budget() -> tuple:
    """全站 embedding 的**两个滚动窗口**判定 ⇒ `(是否放行, 原因, Retry-After 秒)`。

    🔴 **口径照  那份抄**（⛔ 别自己发明）：**全站** · ⛔ 不按 IP · ⛔ 不按 `visitor_id`
    **两个滚动窗口**（只留日窗的话，一个人几秒内就能把当天额度吃光，后面来的真人全进不来）。
    ⚠️ 数值与它的来龙去脉 ⇒ `billing/token_config.py` 里那两行（**反推的，⛔ 不是拍的**）。

    与 `check_global_daily_budget` 同构：**不含** `record_intercept`（拦截记录由调用方负责）。
    触顶动作 = **直接拒绝** ⇒ 调用方回 **429 + `Retry-After`**。
    """
    from billing.token_config import (
        GLOBAL_EMBEDDING_DAILY_LIMIT,
        GLOBAL_EMBEDDING_HOURLY_LIMIT,
    )

    used_day = get_global_embedding_usage(0)
    if used_day >= GLOBAL_EMBEDDING_DAILY_LIMIT:
        return False, (
            f"今日全站 embedding 额度已用完（已用 {used_day:.0f} / "
            f"上限 {GLOBAL_EMBEDDING_DAILY_LIMIT} tokens）"
        ), _seconds_to_tomorrow()

    used_hour = get_global_embedding_usage(1)
    if used_hour >= GLOBAL_EMBEDDING_HOURLY_LIMIT:
        return False, (
            f"最近一小时全站 embedding 用量已达上限（已用 {used_hour:.0f} / "
            f"上限 {GLOBAL_EMBEDDING_HOURLY_LIMIT} tokens）"
        ), 3600

    return True, (
        f"全站 embedding 预算充足（今日剩 {GLOBAL_EMBEDDING_DAILY_LIMIT - used_day:.0f} · "
        f"近一小时剩 {GLOBAL_EMBEDDING_HOURLY_LIMIT - used_hour:.0f} tokens）"
    ), 0


def _seconds_to_tomorrow() -> int:
    """到次日 0 点的秒数。

    ⚠️ 与 `app/main.py::_next_day_reset_ts()` **同源**（那边是 Unix 时间戳版，
    这里要的是**间隔秒数**）。本仓注意过"同一件事两份口径会漂" ⇒ **两处都别改口径**。
    """
    now = datetime.now()
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return max(1, int((tomorrow - now).total_seconds()))


# ==================== 预算通知 ====================

BUDGET_WARNING_THRESHOLD = 0.8  # 80% 阈值

def check_budget_warning(user_name: str) -> dict:
    """
    检查用户是否达到预算警告阈值（80%）。
    如果达到，返回提醒信息；否则返回 None。
    """
    budget = get_user_token_budget(user_name)
    if budget == float("inf"):
        return {"warning": False, "message": None}
    
    used = get_daily_token_usage(user_name)      # Token（原先误用 get_daily_usage_cost 的「元」）
    ratio = used / budget if budget > 0 else 0

    if ratio >= BUDGET_WARNING_THRESHOLD:
        return {
            "warning": True,
            "message": f"⚠️ 您今日已使用预算的 {ratio*100:.0f}%（{used:.0f} / {budget:.0f} tokens），请合理控制调用频率。",
            "ratio": round(ratio, 2),
            "used_tokens": round(used, 0),
            "budget": budget,
        }

    return {"warning": False, "message": None, "ratio": round(ratio, 2)}