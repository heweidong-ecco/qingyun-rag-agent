"""
长期记忆模块：基于 Mem0 的记忆存储与检索
"""
import contextvars
import os

from loguru import logger

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings
from mem0 import Memory

from core.config import LLM_MODEL_FAST
from core.llm_factory import make_llm
from billing.token_tracker import record_usage


# ═══════════════════════════════════════════════════════════════════════════
# 「这一次调用算谁的」—— 记账归属（2026-10-11）
# ═══════════════════════════════════════════════════════════════════════════
#
# **改前**：mem0 用它**自己的** LLM 与 embedder（openai provider）
#   ⇒ 那两条的 token **一个都没记**，连"记错人"都没有。
#   ⇒ 实测：`/agent/memory/add` 回 200，而 LLM 用量、embedding 用量**都没动**、库里**0 行**。
#   影响不止那两个端点 —— `advanced_chat` / `mcp_chat` **每一轮**都调一次 mem0 检索。
#
# **为什么用 ContextVar，不是给它加形参**：mem0 内部调 embedder / LLM 时
#   **不把 user 传进来**（那是 mem0 自己的接口，我们改不了）。
# **已实测（2026-10-11）**：mem0 **就在调用方的执行上下文里**调它们，不是另起线程
#   ⇒ ContextVar 传得进去（探针：分别设 `alice` / `bob`，embedder 两次都读到了）。
_CURRENT_USER: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mem0_current_user", default="system"
)


def _user_of(user_id: str) -> str:
    """`"alice:default"` → `"alice"`。

    `user_id` 是 `f"{user_name}:{memory_space}"` 拼出来的（两个调用点都这么拼）
    ⇒ 取**冒号前**那一段。
    **别写 `split(":")[0]`** —— 那在 `user_name` 本身带冒号时会**静默切错**
       （本仓惯用写法是 `split(sep, 1)`：改的是"最多切一刀"，不是"切几刀"里的 1）。
    """
    return (user_id or "").split(":", 1)[0] or "system"


class RecordingEmbeddings(Embeddings):
    """把 mem0 的 embedding **接到我们自己的实现上**。

    改前 mem0 用**自己的** OpenAI embedder ⇒ 那条路的 token **一分钱都没记**。
    现在走 `rag/embedding_client.get_embedding` ⇒ 与 RAG 侧
    **同一份实现 · 同一份记账 · 同一套 Redis 缓存**（缓存命中时还不花钱）。
    """

    def embed_documents(self, texts):
        get_embedding = _lazy_get_embedding()
        who = _CURRENT_USER.get()
        return [get_embedding(t, user_name=who) for t in texts]

    def embed_query(self, text):
        return _lazy_get_embedding()(text, user_name=_CURRENT_USER.get())


class _UsageRecorder(BaseCallbackHandler):
    """把 mem0 内部那次 LLM 调用**记到账上**（改前完全没记）。

    走 LangChain 的 `on_llm_end` —— 那是**模型层**的回调，
    所以它**不管调用方是谁**（mem0 内部也好、别处也好）都能拿到用量。
    """

    def on_llm_end(self, response, **kwargs):
        try:
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            if not (prompt or completion):
                # 端点没回用量 ⇒ **不编一个**（本仓立场：「不许印没有数据源的数」）
                return
            record_usage(
                model=LLM_MODEL_FAST,
                prompt_tokens=prompt,
                completion_tokens=completion,
                purpose="memory_extract",
                user_name=_CURRENT_USER.get(),
                thread_id="system",
            )
        except Exception as e:
            # **记账失败不许把记忆功能带崩** —— 但也不许静默（打印，不吞）
            print(f"[Token] mem0 记账失败（记忆本身不受影响）: {e}")


def _lazy_get_embedding():
    """延迟导入 `get_embedding` —— 与 `core/cache.py` 同一条理由：**避免循环依赖**。

    模块级导入会让 `agent.memory_store` → `rag.embedding_client` → `core.cache` 这条链
    在 import 期就接死；而本模块**在 import 期本来就要建 mem0 客户端**，是最容易被绕进去的那个。
    """
    from rag.embedding_client import get_embedding
    return get_embedding


# ═══════════════════════════════════════════════════════════════════════════
# Qdrant 落在哪 —— **绝对路径 + 一个显式 env**（2026-10-14 · `①b`）
# ═══════════════════════════════════════════════════════════════════════════
# 🔴 改前是 **`"./.mem0/qdrant"`（相对路径）**，而且它在**模块导入时**就被
#    `Memory.from_config(...)` 解析掉了 ⇒ **落点取决于当时的 CWD**。
#
# 🔴 **这不是理论风险，本机实测已经发生过**（2026-10-14 量出来的）：
#      · `app/.mem0/qdrant/collection/mem0`（2026-08-17，136K）
#      · `./.mem0/qdrant/collection/mem0`（2026-09-17，136K）
#    ⇒ 「从 `app/` 启动」与「从仓库根启动」写出了**两套独立的记忆**，而**没有任何门会报**。
#    ⚠️ 两处**都还在**（本次⛔ 一份都没删）。
#
# ⇒ 现在锚在**文件自己的位置**上（`app/.mem0`）：⛔ 与 CWD 无关，
#    且**默认仍是本仓一直以来的那个落点**（`app/.mem0`）。
# ⚠️ **要改落点时用 `MEM0_DATA_DIR`**（本服务容器要用的话显式设）—— ⛔ 别改这里的默认值。
_MEM0_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".mem0"
)
MEM0_DATA_DIR_ENV = "MEM0_DATA_DIR"


def mem0_data_dir() -> str:
    """Qdrant 的落盘根目录（**每次现读** env —— 与 `core.db.backend_name()` 同一个理由：
    缓存会让 `monkeypatch.setenv` 那一类测试**静默无效**）。"""
    return os.getenv(MEM0_DATA_DIR_ENV) or _MEM0_ROOT


# 初始化 Mem0 客户端
# Mem0 支持本地模式（数据存储在本地文件）和云端模式，这里使用本地模式进行开发
# 注意：mem0ai>=2.0 的 Memory.__init__ 只接受 MemoryConfig 对象，不再接受 dict，
# 需使用 Memory.from_config(dict) 构造。
mem0_client = Memory.from_config(
    # 本地模式配置 —— 落点见上面那段（**绝对路径**，⛔ 不再跟 CWD 跑）
    {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "path": os.path.join(mem0_data_dir(), "qdrant"),
                "collection_name": "mem0",
            }
        },
        # 2026-10-11：LLM 与 embedder **都换成 `langchain` provider** ——
        #    唯一目的是**把它们接到我们自己的记账上**（改前那条路一个 token 都不记）。
        #    `provider: "langchain"` 是 mem0 **自带的**（`mem0/llms/langchain.py` /
        #       `mem0/embeddings/langchain.py`），它只要求：LLM 给 `BaseChatModel`、
        #       embedder 给 `Embeddings`。
        #    **三条前提都实测过**（不是照文档推的）：
        #       ① `make_llm()` 返回的裸 `ChatOpenAI` **就是** `BaseChatModel`；
        #       ② 它**收 `callbacks=`**（`llm_factory` 的 `**extra` 会透传给 `ChatOpenAI`）；
        #       ③ 两个适配器在**本仓的 langchain 1.x** 下**都 import 得动**
        #          （mem0 那两份写的是 `from langchain.embeddings.base import Embeddings`，
        #           而本仓已升 1.x —— 这条**差点**就是拦路虎，先验了才知道没事）。
        "llm": {
            "provider": "langchain",
            "config": {
                # 角色 = `("fast", "agent")` —— **归「中间步骤」**（输出是一串事实，不是答案）。
                #    它**必须进 `test_max_tokens_wiring.py` 的裁定表**，否则那道门会红
                #    （那条门就是逼你**当场分类**、不许默认填一个）。
                # 改前那版**没设 `max_tokens`**（吃端点默认）⇒ 接小是**行为变更**，
                #    已**端到端实测**过（`/agent/memory/add` 真跑通、事实真进了库）。
                "model": make_llm("fast", "agent", callbacks=[_UsageRecorder()]),
            },
        },
        "embedder": {
            "provider": "langchain",
            "config": {"model": RecordingEmbeddings()},
        },
    }
)


def add_user_memory(user_id: str, content: str):
    """
    添加一条用户记忆。
    Mem0 会自动提取关键信息、去重、并更新已有记忆。

    2026-10-11：**进去之前先设「这次算谁的」** ——
    mem0 内部那次 LLM（抽事实）与 embedder 的 token 由此**记到真实用户头上**
    （改前**完全没记**，见模块头那段）。
    `finally` 里 `reset` —— 别改成"设完不管"：**并发请求会串账**。
    """
    token = _CURRENT_USER.set(_user_of(user_id))
    try:
        mem0_client.add(content, user_id=user_id)
    finally:
        _CURRENT_USER.reset(token)


def search_user_memory(user_id: str, query: str, top_k: int = 3) -> list[str]:
    """
    搜索与当前查询相关的用户记忆。
    返回相关记忆的文本列表。

    2026-10-11：同 `add_user_memory` —— **设好归属再进去**。
    这条**每一轮对话**都会被调到（`advanced_chat` / `mcp_chat` 的 `inject_memories_to_prompt`）
    ⇒ 它不记账的话，那条 embedding 就是**每轮都白花**。
    """
    # 2026-09-20 修（依赖漂移）:此前写的是 `search(query, user_id=…, limit=…)`。
    #    mem0 **2.0.20** 的签名是 `search(query, *, top_k=20, filters=None, …)` ——
    #    ① `user_id` 不再是顶层参数（要走 `filters`）② `limit` 已改名 `top_k`。
    #    线上实测报错:`ValueError: Top-level entity parameters frozenset({'user_id'})
    #    are not supported in search(). Use filters={'user_id': '...'} instead.`
    #    它让**二代/三代 Agent 直接 500**（两者都在入口注入长期记忆）。
    #    回归测试:`app/tests/test_agent_repairs.py::test_search_user_memory_matches_mem0_2x_signature`
    token = _CURRENT_USER.set(_user_of(user_id))
    try:
        results = mem0_client.search(query, filters={"user_id": user_id}, top_k=top_k)
    finally:
        _CURRENT_USER.reset(token)
    # 2026-09-20 第二处（同一依赖漂移的另一半）:**返回形状也变了**。
    #    mem0 2.x 的 search() 返回 **`{"results": [...]}`**（dict），**不是 list**。
    #    此前直接 `for r in results` ⇒ 遍历 dict 拿到的是**键（字符串）**
    #    ⇒ 线上实测 `TypeError: string indices must be integers`。
    #    这一处是**第一次没修完**才暴露的：我当时只验了"不抛异常 + len=1"，
    #       而**一个只有 1 个键的 dict，len() 也是 1** ⇒ 验了"没炸"，没验"形状对不对"。
    items = results.get("results", []) if isinstance(results, dict) else (results or [])
    if items:
        return [r["memory"] for r in items if isinstance(r, dict) and "memory" in r]
    return []


def memory_block(user_id: str, query: str) -> str | None:
    """检索长期记忆，拼成一段**能直接塞进 prompt 的文本**；一条都没检索到 ⇒ `None`。

    **为什么要有它**（2026-10-13）：「外部检索与工具Agent编排」那一页的主链
       （`/agent/langgraph_chat` ⇒ `agent_graph`）原先**不注入 mem0** ⇒ 那一页不能说"有记忆"。
       给它补上时，需要一份**不依赖图状态**的取法 —— 本模块是 `search_user_memory` 的**家**，
       记忆怎么渲染成文本的**唯一出处**放这里最顺。

    **为什么不把那两份历史副本（`agent_graph_advanced.py:66`
       `agent_graph_advanced_learning.py:115` 的 `inject_memories_to_prompt`）搬过来合并**：
       它们**不是**死代码 —— 是 **5 个测试文件**在用的 monkeypatch 缝
       （`monkeypatch.setattr(aga, "inject_memories_to_prompt", …)`，默认 `raising=True`）。
       合并 = 挪缝 ⇒ 那些补丁**静默失效**（本仓  就是这么烂的）。
       ⇒ 本轮**只加这一份新用法**，那两处**一个字不动**；重复已单独立账（§六）。

    它**会花钱**：`search_user_memory` 走一次 embedding（起记在**真实用户**头上）
       ⇒ **必须排在预算门之后**调用（本仓  的教训：门之前花掉的 embedding 会被当场丢弃）。

    @returns 一段文本（末尾无空行）或 `None` —— 调用方按"有值才拼进 prompt"处理，
             别把空串拼进去（那会把"没有记忆"变成"有一句空白的记忆"）。
    """
    # **必须 fail-open** —— 记忆是「有更好」的锦上添花：
    #    取不到（embedding 的 key 不对 / 网络不通 / mem0 或向量库挂了…）只该**少一段上下文**，
    #    **不许把整条链打挂**。这一条是拿一次**真实的 CI 全红**换来的（2026-10-13）：
    #    本图（`agent_graph`）补上这个调用之后，CI 里 **14 条"驱动真图"的用例全红**，
    #    根因就是 DashScope 的 key 在 CI 里是**占位值** ⇒ 这一句抛 401
    #    **异常从图节点里穿出来** ⇒ 整条请求挂。
    #    本机**看不见**：本机有真 key ＋ Redis 里有 embedding 缓存（同问句早算过）。
    #    追因与判据 ⇒ （§六）
    try:
        memories = search_user_memory(user_id, query)
    except Exception as e:                      # noqa: BLE001 —— 见上：**任何**失败都只该降级
        # **要留痕，不是静默吞掉** —— 静默的话，"记忆一直没生效"没有任何人会发现。
        logger.warning(
            f"[memory] 长期记忆检索失败 ⇒ 本次不注入（fail-open）：{type(e).__name__}: {e}")
        return None
    if not memories:
        return None
    return ("以下是与用户相关的长期记忆，请参考这些信息来个性化你的回答：\n"
            + "\n".join(memories))