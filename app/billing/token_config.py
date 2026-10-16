"""额度配置的【唯一落点】。
"""
import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


# ==================== 单次上限（token）· 不分角色 ====================
# 行业区间 500–2000（见 后端补齐清单 B7 附的检索来源）。
# 取上沿而不是"中和"：压低到 1800 以下**会开始截断答案**。
MAX_TOKENS_ANSWER = _int("TOKEN_MAX_ANSWER", 2000)   # RAG 答案生成 / WS agent
MAX_TOKENS_AGENT = _int("TOKEN_MAX_AGENT", 1024)     # Agent 对话 / planner / 质检
# 查询改写**已有自己的值**（query_rewriter.py:65/143），此处只登记、不改行为
MAX_TOKENS_REWRITE_VARIANTS = _int("TOKEN_MAX_REWRITE_VARIANTS", 200)
MAX_TOKENS_REWRITE_INTENT = _int("TOKEN_MAX_REWRITE_INTENT", 800)

# ==================== 分层上限（token）· 由后续计划接线 ====================
SESSION_TOKEN_LIMIT = _int("SESSION_TOKEN_LIMIT", 50000)          # B8（①b）
GLOBAL_DAILY_TOKEN_LIMIT = _int("GLOBAL_DAILY_TOKEN_LIMIT", 15_000_000)  # B10（①b）

# ============================================================================
# 🔴 全站【embedding】的两个滚动窗口（·  §二·8 · 作者裁「甲」）
# ============================================================================
GLOBAL_EMBEDDING_HOURLY_LIMIT = _int("GLOBAL_EMBEDDING_HOURLY_LIMIT", 100_000)
GLOBAL_EMBEDDING_DAILY_LIMIT = _int("GLOBAL_EMBEDDING_DAILY_LIMIT", 1_000_000)

# ## 先更正一条【我读错的】事实（作者 原话）
# > 「我**没有**在百炼控制台勾选『免费额度用完即停』，**免费的用完了，会自动转成付费的，
# >   API 不会停**，我设置的是**使用金额上限**，**付费超过了金额上限才会停**」

# ==================== 角色日预算（token）· **按池分开** ====================

#: **LLM 池**的日额度 —— 生成 / 决策 / 改写那一路。
LLM_DAILY_TOKEN = {
    #    起因是一条实测：**一次 `/agent/memory/add` 要 8112 个 LLM token**
    #    （mem0 抽事实那次）⇒ 按 10 000 算，访客**点一次加记忆就打掉八成**。
    "free": _int("DAILY_TOKEN_LLM_FREE", 50_000),
    "premium": _int("DAILY_TOKEN_LLM_PREMIUM", 500_000),
    "admin": _int("DAILY_TOKEN_LLM_ADMIN", 500_000),   # admin = premium（沿 那条口径）
}

#: **Embedding 池**的日额度 —— 向量化那一路。
EMBEDDING_DAILY_TOKEN = {
    "free": _int("DAILY_TOKEN_EMBEDDING_FREE", 10_000),
    "premium": _int("DAILY_TOKEN_EMBEDDING_PREMIUM", 100_000),
    "admin": _int("DAILY_TOKEN_EMBEDDING_ADMIN", 100_000),   # 同上
}

# **本模块【不再】导出 `ROLE_DAILY_TOKEN`**（删）。
#    它是"每个角色一个数"的旧口径 —— 而两池分开之后，**那个数已经不存在了**
#    （问"free 的日额度是多少"必须再问"哪个池"）。
#    **删它而不是留个别名**：留别名的那一刻，读的人会以为"两个池都是这个数"。

# 未登记角色的兜底日预算（迁自 `token_tracker.py:283`）
#   改前它 **= premium = 最高档** ⇒ `get_user_token_budget()` 里的
#   `.get(role, DEFAULT_DAILY_TOKEN_BUDGET)` 一旦被走到（角色名拼错 / 新角色没登记），
#   用户**静默拿到全额最高额度**，不报错、不告警 —— 与 / 一路的
DEFAULT_DAILY_TOKEN_BUDGET = _int("DEFAULT_DAILY_TOKEN_BUDGET", 10_000)

# ==================== 模型单价（元 / 1000 tokens） ====================
# 迁自 `token_tracker.PRICING`（:50）。加新模型时**只改这里**。
MODEL_PRICING = {
    "qwen-turbo": {"prompt": 0.003, "completion": 0.006},
    "qwen-plus": {"prompt": 0.008, "completion": 0.016},
    #    单价来源：**阿里云百炼「模型调用计费」页**（`help.aliyun.com/zh/model-studio/model-pricing`）
    #       —— **经搜索核到 0.125 元/百万输入 token**，与  §2.1 记的同一个数。
    #       本机**打不开那个页**（域名在当前网络环境下取不到）⇒ 记的是**搜索命中的官方页摘要**，
    #          谁要更硬的凭据就去控制台账单页复核一次。
    #    🔴 **已删**（B 段，与「`.env` 翻 flash」同一笔）—— 上面那句**【暂时保留】**
    "qwen3.7-text-embedding-flash": {"prompt": 0.000125, "completion": 0},
    #    （`docs/契约/环境变量.md:70`），而原先这张表**只有 qwen 系列**
    #    金额落到兜底价，**且没有任何地方看得出它是兜底**。
    # 单价来源：DeepSeek 官方人民币口径「输入 1 元 / 输出 2 元 每百万 token」
    #    ⇒ 本表单位是元/1000 ⇒ 0.001 / 0.002。
    "deepseek-v4-flash": {"prompt": 0.001, "completion": 0.002},
}
#    —— 把它排在 **A 段**，但那样会**同时**踩两件事：
#      ① A 段的定义是「行为与改前**逐字相同**」（自己第一句就写着），
#         而删掉**在用模型**的价 ⇒ 每次 embedding 记账从 0.0005 落到兜底 0.003（**高报 6 倍**）
DEFAULT_MODEL_PRICING = {"prompt": 0.003, "completion": 0.006}

# ==================== 花费上限（元）· 多级预算的第一、二级 ====================
MAX_SINGLE_CALL_COST = _float("MAX_SINGLE_CALL_COST", 0.5)
MAX_THREAD_COST = _float("MAX_THREAD_COST", 5.0)

# ==================== 限流参数（S6） ====================
# 与上面的额度参数**同型**（原先都写死在 `rate_limiter.py:129/132`）⇒ 一起收口。
GLOBAL_LIMIT_RATE = _float("GLOBAL_LIMIT_RATE", 100.0)      # 全局：每秒补充令牌数
GLOBAL_LIMIT_CAPACITY = _int("GLOBAL_LIMIT_CAPACITY", 150)  # 全局：桶容量（突发上限）
USER_LIMIT_RATE = _float("USER_LIMIT_RATE", 3.0)            # 单用户：每秒补充
USER_LIMIT_CAPACITY = _int("USER_LIMIT_CAPACITY", 20)       # 单用户：桶容量

RATE_LIMIT_BUCKET_TTL = _int("RATE_LIMIT_BUCKET_TTL", 60)

