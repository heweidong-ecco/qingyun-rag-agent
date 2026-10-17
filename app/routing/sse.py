"""SSE（Server-Sent Events）**共享层** —— 本仓所有流式端点的骨架。

> （`api_v1_agent.py::langgraph_chat_stream` · `api_v1_rag.py::stream_search`），
> 而**第 3 遍就要开始抄了** —— 本模块把"抄不得的那部分"收成一个地方。
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Callable, Collection, Optional

#  （真服务实测）：关上游的流要用它护住（约束②）。
import anyio

from fastapi.responses import StreamingResponse
from loguru import logger

from core.metrics import track_stream_cancel

__all__ = [
    "DONE_FRAME",
    "SSE_HEADERS",
    "sse_frame",
    "sse_response",
    "graph_message_text",
    "llm_chunk_text",
    "sse_stream",
]


# ==================== 帧与响应（叶子，无状态） ====================

#: 结束哨兵。**不由骨架自动补** —— 它属于各链的收尾契约（见模块 docstring）。
DONE_FRAME = "data: [DONE]\n\n"

#: `X-Accel-Buffering: no` 不能省（约束④）—— 反代会把 SSE 攒着发。
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_frame(payload: dict, *, ensure_ascii: bool = False) -> str:
    """把 dict 编成一帧 SSE。

    **`ensure_ascii` 现在是一个"全仓都传同一个值"的参数**：

    * `False`（**默认**）= 中文原样出（UTF-8）。**全仓 6 条流式端点一律它**。
    * `True` = 中文变 `\\uXXXX`。⚠️ **现在没有任何调用点用它**。

    🔴 **保留这个参数是【有意】的** —— 它是  那次统一的**反悔路径**（一个参数就能改回去）。
       ⛔ 但**别再加一个传 `True` 的调用点**：那会把"全仓一个口径"重新拆成两种
       （`app/tests/test_sse_encoding.py` 会当场红）。

    📄 **沿革**：抽公共层时RAG 传的是 `True`，为了**不改线上字节**；
       作者裁定**统一成 `False`**（理由：流式输出**人会直接读**，
       `\\uXXXX` 对人不可读；而两种编码 **JSON 解码后值相同** ⇒ 功能无感）。
       ⚠️ **代价**：那条"逐帧等价"从「**字面**为真」降成「**解码后值相同**」（`sse.md` 已同步改口径）。
    """
    return f"data: {json.dumps(payload, ensure_ascii=ensure_ascii)}\n\n"


def sse_response(source: AsyncIterator[str]) -> StreamingResponse:
    """包成 `text/event-stream` 响应（三个头都在 `SSE_HEADERS` 里）。

    **每次 copy 一份** —— 别把 `SSE_HEADERS` 这个模块级 dict 直接传进去：
        `StreamingResponse` 会**持有并改写**它自己的 `headers`，共享同一个 dict
        等于让所有响应**串台**（而且是在"某天多了一条路由"时才炸）。
    """
    return StreamingResponse(
        source,
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )


# ==================== 两个现成的 extractor ====================

def graph_message_text(
    item, *, nodes: Collection[str]
) -> Optional[str]:
    """LangGraph `stream_mode="messages"` 的一块 ⇒ 该发出去的文本（`None` = 这块不发）。

    两种 `None` 都要挡，**两种都是实测撞出来的**：

    1. **节点不在白名单里** —— 图里**不只有"该流"的节点**：
       * `tools` / `approval` 节点的 `ToolMessage` 内容会**混进正文**；
       * **更隐蔽的一类**：**返回 `messages`（或整个 state）的节点会额外发一块「非 token 的整块」**。
         实测 `advanced_chat` 的 `supervisor`（它 `return state`）会把它收到的
         **用户提问原文**当"新消息"发出来 —— 不加白名单，**用户会先看到自己的问题被回显一遍**。
       * **别用"这块是不是 token"当判据**（没有可靠的判别字段）⇒ **就按节点名收**。

    2. **`content` 为空** —— `tool_call` 是**碎片化**到达的，一次调用会来 2–3 个空 `content` 块，
       不过滤 ⇒ 前端收到一串空白帧。

    **`item` 有两种形状**（`subgraphs` 开关换的，实测）：

    | 调用方 | `astream(...)` 的 `subgraphs` | `item` |
    |---|---|---|
    | 无子图的链（本仓 B / C） | 不传（`False`） | `(chunk, meta)` |
    | **有子图的链**（本仓 A） | **必须 `True`**，否则**一个字都流不出来** | `(namespace, (chunk, meta))` |

    ⇒ 本函数**负责归一化**，端点里别再解一次。

    **`meta["langgraph_node"]` 报的是【裸内层名】**（实测：子图里叫 `agent`，
    **不是** `react_dept:agent`）⇒ 白名单里写**内层名**。而 `get_graph(xray=1)` 给的是
    `react_dept:agent` ⇒ 两者对不上，**要按 `split(":")[-1]` 后缀比**（见 `STREAMABLE_NODES` 的断言）。
    """
    # 三种形状都认（langgraph 小版本之间有差异，别只写实测到的那一种）：
    #    (ns, chunk, meta)    ← 另一些版本
    #    (ns, (chunk, meta))  ← subgraphs=True（本版本实测。`ns` 自己是 tuple）
    #    (chunk, meta)        ← subgraphs=False
    if len(item) == 3:
        _, chunk, meta = item
    elif isinstance(item[0], tuple):
        _, (chunk, meta) = item
    else:
        chunk, meta = item

    if meta.get("langgraph_node") not in nodes:
        return None
    return chunk.content or None


def llm_chunk_text(chunk) -> Optional[str]:
    """**裸 LLM** 的一块（不是图）⇒ 该发出去的文本。

    给 `plan_execute` 那条用（它不是图，`astream` 出来直接是 `AIMessageChunk`）。
    空 `content` 同样要挡 —— 理由与 `graph_message_text` 第 2 条完全一样。
    """
    return chunk.content or None


# ==================== 骨架 ====================

async def sse_stream(
    open_upstream: Callable[[], AsyncIterator],
    *,
    endpoint: str,
    extract: Optional[Callable[[object], Optional[str]]] = None,
    on_complete: Optional[Callable[[list], AsyncIterator[str]]] = None,
    on_incomplete: Optional[Callable[[list, str], None]] = None,
    on_error: Optional[Callable[[BaseException, list], AsyncIterator[str]]] = None,
    ensure_ascii: bool = False,
    chunk_delay: float = 0.0,
) -> AsyncIterator[str]:
    """流式骨架：**打开上游 → 逐块发帧 → 收尾**。五条约束都在这一个函数里（模块 docstring）。

    Args:
        open_upstream: **同步**可调用，返回一个「**异步可迭代 + 有 `.aclose`**」的对象
            （`graph.astream(...)` / `llm.astream(...)` / 自建桥接器）。签名里**不出现 LangGraph 类型**
            ⇒ 本层对"上游是什么"一无所知。
            **它必须返回同一个对象**（不能把 `astream(...)` 内联在 `async for` 里）——
            骨架要拿这个句柄去 `aclose`（约束②）。
        endpoint: Prometheus 的 label + 日志标识（别传函数名，也别让两条链共用一个值）。
        extract: `(item) -> str | None`；返回 falsy ⇒ **跳过这一块**。`None` ⇒ item 原样当文本。
        on_complete: **async 生成器**，`(collected) -> AsyncIterator[str]`。**只有正常跑完才进**，
            负责产**整条收尾尾巴**（汇总帧 / `[DONE]` / sources…）。骨架不替你补 `[DONE]`。
        on_incomplete: **同步** callable，`(collected, status) -> None`。**两种"没走完"各调一次**
            必须是同步的 —— 这正是它能躲开"二次投递的取消"的原因（约束①）。
            **正常跑完一次都不调** —— 那条路归 `on_complete`。
            **两条出口不会各调一次**：异常那条会 `return`，`finally` 里那块只在
               `outcome == "cancelled"` 时进 ⇒ **一次出口只调一次**。
        on_error: **async 生成器**，`(exc, collected) -> AsyncIterator[str]`。
            `None` ⇒ 默认：发一帧 `{"error": …}` + `[DONE]`，然后结束。
            `/rag/stream_search` 显式传它**只发 error 帧**（旧行为：**错误路径没有 `[DONE]`**）——
            理由不是保守：**`[DONE]` 会被读成"正常收尾"**，RAG 今天靠"没有 `[DONE]`"分辨出错。
        ensure_ascii: 透传给 `sse_frame`（见它的 docstring）。
        chunk_delay: 每发一块之后 `sleep` 这么久。`/rag/stream_search` 传 `0.01`（**保持旧节奏**）。

    Yields:
        SSE 帧字符串。
    """
    upstream = None
    # 默认按「被取消」算 —— 只有跑到收尾才改 `"done"`（约束③：2.4 分支抛的是 `GeneratorExit`，
    #    它**进不了** `except Exception`，只有 `finally` 收得住）。
    outcome = "cancelled"
    collected: list = []

    try:
        # 在 `try` **里面**打开：`astream` 本身就可能抛（配置错 / 图没编译）。
        upstream = open_upstream()
        async for item in upstream:
            text = extract(item) if extract is not None else item
            if not text:
                continue
            collected.append(text)
            yield sse_frame({"content": text}, ensure_ascii=ensure_ascii)
            if chunk_delay:
                # 这一行**不再是"让出控制权"**（`async for` 本身就是 await 点）——
                #    它只剩「限速」这一个作用。留着是为了**不改旧节奏**。
                await asyncio.sleep(chunk_delay)

        # 与两条旧实现一致：**在收尾之前**置 `done`。
        #    ⇒ 收尾期（`aget_state` / 落历史 / 发 sources）被取消，**不计入取消数**。
        #    别"顺手修正"成"收尾完成才算 done" —— 那会改动一条**没有任何用例覆盖**的边界。
        outcome = "done"
        if on_complete is not None:
            async for frame in on_complete(collected):
                yield frame

    except asyncio.CancelledError:
        # 客户端断开。**不许吞**（吞掉外层会以为这是"正常结束"）；
        # **不许在这里 `yield`**（接收方已经走了 —— 且**实测真发得出去一帧**）。
        raise
    except Exception as exc:
        outcome = "error"
        # 别让异常**静默**变成"流自然结束" —— 那前端看到的是"答案说了一半就没了"，
        #    而**没有任何错误信号**。
        logger.error(f"[stream] 生成出错 endpoint={endpoint}: {exc}")
        #：留痕排在**任何 `yield` 之前** —— 理由与约束① 同源（`yield` 也是 await 点，
        #    被"二次投递的取消"打断 ⇒ 后面的收尾一件都不跑，而错误帧照样发得出去）。
        if on_incomplete is not None:
            on_incomplete(collected, "error")
        if on_error is not None:
            async for frame in on_error(exc, collected):
                yield frame
        else:
            yield sse_frame({"error": str(exc)}, ensure_ascii=ensure_ascii)
            yield DONE_FRAME
        return

    finally:
        # **顺序 = 真服务实测改的**（约束①）：
        #    `await upstream.aclose()` 会被**二次投递的取消**打断 ⇒ 排在它后面的收尾**一件都不跑**。
        #    ⇒ 三件**全同步**，任何 `await` 都在它们**后面**。
        if outcome == "cancelled":
            track_stream_cancel(endpoint)
            logger.info(f"[cancel] 客户端断开，已停止生成并关闭上游流 endpoint={endpoint}")
            if on_incomplete is not None:
                on_incomplete(collected, "cancelled")

        # 关上游（约束②）：不关 ⇒ 上游**继续生成、继续计费**，而前端看起来一切正常
        # （它只是不显示了）—— 硬门 C 标「最容易假完成」就是这个形态。
        if upstream is not None:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()
