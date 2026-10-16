"""
查询改写模块
使用 LLM 对用户问题进行扩展和优化，提升检索召回率。
（带 Redis 缓存）
优化查询：结合对话历史补全上下文、转书面语、消解指代。
"""
import hashlib
import json
from openai import OpenAI
import redis
from loguru import logger

# 复用现有的 Redis 客户端（与 cache.py 中相同配置）
# 从 config 导入 host/port，以正确应用本地开发时 localhost 的覆盖
# 2026-09-20 删（D1/pyflakes 报 redefinition）：此处原有**两行**导入，
#    第一行 `from config import REDIS_HOST, REDIS_PORT` 被**第二行完全覆盖**
#    （第二行是它的超集），且覆盖前从未使用 ⇒ 删第一行、**保留第二行**（就是下面这行）。
from core.config import REDIS_HOST, REDIS_PORT, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_FAST

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True
)
# 复用现有的 LLM 客户端配置
client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL
)

REWRITE_MODEL = LLM_MODEL_FAST

# 缓存过期时间：1小时
CACHE_TTL = 3600

def _get_cache_key(prefix: str, text: str, extra: str = "") -> str:
    """生成缓存键"""
    raw = f"{prefix}:{text}:{extra}"
    return "rewrite:" + hashlib.md5(raw.encode()).hexdigest()

def expand_query(original_query: str, num_variants: int = 3, *,
                 user_name: str, thread_id: str) -> list[str]:
    """生成多个不同表述的查询变体，用于扩大检索范围（带缓存）。

    2026-09-20 修：原 docstring 只有「生成查询变体（带缓存）」，
       而函数体里**另有一段孤立的 docstring**（"用于扩大检索范围"）——
       它躺在缓存早退分支之后，是**空操作**。已合并到这里。

    2026-10-05：加**必填** keyword `user_name`—— 本函数真调 LLM(付费)，
       **不给默认值**：给了就等于允许「静默记成 `"unknown"`」= 假记账
       （明文把 `.get(...,"unknown")` 当反例）。漏传 ⇒ `TypeError`，当场炸。
    """
    cache_key = _get_cache_key("expand", original_query, str(num_variants))
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    prompt = f"""你是一个查询扩展助手。请将用户的问题改写成 {num_variants} 个不同表述但语义相同的查询。
每个查询一行，不要编号，不要任何额外说明。

用户问题：{original_query}

改写结果：
"""
    response = client.chat.completions.create(
        model=REWRITE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,  # 稍高温度以生成多样性
        max_tokens=200
    )
    # 2026-10-05 记账：本模块用的是**裸 `openai.OpenAI`** ⇒ 响应带 `.usage`，
    #    而**没有** `usage_metadata` ⇒ 必须走 `record_usage`，**不能**用
    #    `record_from_response`（它的判据是 `usage_metadata`，对裸客户端**恒 False 静默跳过**
    #    —— 那正是  修过的假记账）。紧贴 `create()` 之后：钱已经花了。
    record_usage(
        model=response.model,                       # 不写死 REWRITE_MODEL：本仓按错单价记过账
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        purpose="query_expand",
        user_name=user_name,
        # 🔴 （2026-10-16）：**必须带会话** —— 不带就落 `record_usage` 的默认桶 `"unknown"`，
        #    而会话闸查的是调用者传的那个 ⇒ **闸恒看到 0**（那条路上的会话上限永远不触发）。
        thread_id=thread_id,
    )
    lines = response.choices[0].message.content.strip().split("\n")
    # 过滤空行，并确保包含原始查询
    variants = [line.strip() for line in lines if line.strip()]
    if original_query not in variants:
        variants.insert(0, original_query)
    result = variants[:num_variants + 1]

    # 写入缓存
    redis_client.set(cache_key, json.dumps(result, ensure_ascii=False), ex=CACHE_TTL)
    return result

# 2026-10-05：这个 import 自建立起就**在，却一次没被调用过**——
#    「接了一半」的形态：import 在、调用不在，而**接口一切正常**（本仓  栽过同款）。
#    现在 `expand_query` / `rewrite_query` 各调它一次。
#    本模块是**全仓唯一**用裸 `openai.OpenAI` 的地方（另 15 处是 `ChatOpenAI`）
#    ⇒ 记账入口**只能**是 `record_usage`（响应带 `.usage`，**没有** `usage_metadata`）。
from billing.token_tracker import record_usage # Token统计模块


def _history_lines(conversation_history) -> list:
    """把对话历史规整成**字符串列表**（最多取最近 5 条）。

    为什么需要这个函数（2026-09-16 修）：
        历史的**真值类型是 `list[dict]`** —— `schemas.py` 的 `QuestionRequest.conversation_history`
        就是这么声明的，`cache.py:get_chat_history` 从 Redis 读回来也是 dict 列表。
        但本模块（以及 `hybrid_search.py`）的历史签名曾写成 `list[str]`，
        于是**按 schema 传 dict 的调用方在这里 `"|".join([dict,…])` → TypeError ⇒ 500**。
        （默认模式 `accurate_norerank` 本身就开着改写，所以这条路径是**可达的**，不是边界情况。）
        两种形态都吃，即可消除这个 500。
    """
    lines = []
    for item in conversation_history or []:
        if isinstance(item, dict):
            content = item.get("content")
            if content:
                lines.append(f"{item.get('role', 'user')}: {content}")
        else:
            lines.append(str(item))
    return lines[-5:]          # 只取最近 5 轮


def rewrite_query(original_query: str, conversation_history=None, *,
                  user_name: str, thread_id: str) -> str:
    """优化查询（带缓存）

    做什么：补全上下文、转书面语、纠正口语化表达；提供了对话历史时会尝试消解指代。

    `conversation_history` 接受 `list[dict]`（`{"role","content"}`，= schema 声明的形态）
    或 `list[str]`（旧形态）—— 两种都由 `_history_lines` 规整。

    2026-09-20 修：上面"做什么"那句，原先是**函数体中段一个孤立的字符串**（空操作）。

    2026-10-05：加**必填** keyword `user_name`—— 同 `expand_query`，
       不给默认值（默认值 = 允许假记账）。
    """
    # 将历史序列化成字符串作为缓存键的一部分
    history_lines = _history_lines(conversation_history)
    history_str = "|".join(history_lines)
    cache_key = _get_cache_key("rewrite", original_query, history_str)
    cached = redis_client.get(cache_key)
    if cached:
        return cached
    history_text = ""
    if history_lines:
        history_text = "\n对话历史：\n" + "\n".join(history_lines)

    prompt = f"""你是一个查询优化助手。请将用户的问题改写成更适合文档检索的书面语形式。
要求：
- 如果问题中有指代不明的代词（如“它”、“这个”、“那个”），请根据对话历史将其替换为明确的对象。
- 如果问题是省略句，请结合历史补全完整的语义。
- 将口语化表达转为正式书面语。
- 保持原始语义不变。
- 只输出改写后的问题，不要任何额外说明。{history_text}

用户问题：{original_query}

改写结果：
"""
    response = client.chat.completions.create(
        model=REWRITE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,  # 低温以确保语义不变
        # 800 而非 200:REWRITE_MODEL 为**推理型**模型(如 deepseek-v4-flash),推理过程先吃
        # 额度;200 一旦被推理吃满,响应即 finish_reason=length 且 **content 为空**
        # (2026-09-11 实测:同一 prompt 15 次里 14 次为空)。
        max_tokens=800
    )

    result = (response.choices[0].message.content or "").strip()

    # 2026-10-05 记账：紧贴 `create` 之后、**空值早退之前** ——
    #    下面那条「改写成空 ⇒ 回退原问题」的分支**也要先记**：钱已经花了，
    #    回退只影响**返回什么**，不影响**账上花了多少**。
    #    用 `record_usage` 不用 `record_from_response`，理由同 `expand_query`（裸客户端）。
    record_usage(
        model=response.model,                       # 不写死 REWRITE_MODEL
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        purpose="query_rewrite",
        user_name=user_name,
        thread_id=thread_id,      # 🔴 ：同 `expand_query`，不带就落 `"unknown"`
    )

    # 改写成空必须**回退到原问题**:否则会拿「空查询」去检索 → 召回到无关文档 →
    # 被判「资料中没有」而拒答。静默降级比报错更危险 —— 这里不再静默(留告警)。
    if not result:
        logger.warning(
            "query_rewriter: 改写返回空(finish_reason={}),已回退原问题: {}",
            response.choices[0].finish_reason, original_query,
        )
        return original_query

    # 写入缓存(只缓存**非空**结果;空值不写,避免被当成"已缓存")
    redis_client.set(cache_key, result, ex=CACHE_TTL)
    return result