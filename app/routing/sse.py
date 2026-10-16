"""SSE（Server-Sent Events）**共享层** —— 本仓所有流式端点的骨架。

> 2026-10-04 新建（剩余 4 条链）。**在它之前，"这幅骨架"在本仓被内联抄了 2 遍**
> （`api_v1_agent.py::langgraph_chat_stream` · `api_v1_rag.py::stream_search`），
> 而**第 3 遍就要开始抄了** —— 本模块把"抄不得的那部分"收成一个地方。

---

## 这个模块**存在的理由**：那 5 条**不能交给调用方**的约束

骨架里凡是"顺序"和"关流方式"，**都是实测撞出来的**，不是设计偏好。
交给调用方自己写 ⇒ **一定会有人写反**（本仓**已经写反过一次**，）。

| # | 约束 | 出处（都是实测，不是推断） |
|---|---|---|
| ① | **同步收尾（计数 → 日志 → `on_incomplete`）必须排在【任何 `await` 之前】** |：`await stream.aclose` 会被**二次投递的取消**打断 ⇒ 排在它后面的收尾**一件都不跑**，而**单测全绿**（假流的 `aclose` 不抛）。只在**晚切**（用户已看到字再点停止）时露出来 |
| ② | **关上游必须包 `anyio.CancelScope(shield=True)`** | 同上。取消作用域在**每个 await 点反复投递** ⇒ 不护住，"关"这个动作每次都半途而废 |
| ③ | `except asyncio.CancelledError: raise` —— **不吞 · 不在里面 `yield`** | /：旧实现在取消时还发了一帧 `[DONE]`，**实测真发得出去**（接收方已走，发出去只会让"已取消"与"正常收尾"在**帧层面长得一样**） |
| ④ | `X-Accel-Buffering: no` **不可省** | 有反代（Nginx / Cloudflare）时它会把 SSE **攒着发** ⇒ 本地直连一切正常、**上线后变成假流式** |
| ⑤ | 汇总/状态**只从图的最终状态取**（`aget_state`），**不是"把流过的块攒起来"** |  **真服务**撞出来的：攒块会让上一轮的 `tool_calls` 混进来 ⇒ 判成 `pending_approval`，而图**早就跑完了** ⇒ 前端**永远等一个不会来的批准** |

**③ 有两副面孔**：Starlette 2.3 抛 `CancelledError`，2.4 走 `GeneratorExit`（**不是** `Exception` 的子类）。
⇒ `outcome` **默认按「被取消」算**，只有跑到收尾才改 `"done"` —— 这样**两条分支都记得上**。

---

## 它**不**负责什么（别往里塞）

| 不进共享层 | 为什么 |
|---|---|
| `check_session_token_budget` / `circuit` | 必须在**建生成器之前**拒（触顶要回 4xx，不是"HTTP 200 + 流一半断"）；且两条 AST 守卫钉着它们在**端点函数体**里（`app/tests/test_session_budget_wiring.py` · `app/tests/test_breaker_wiring.py`） |
| `register` / `resolve`（待接管队列） | 四条链各不同；且靠**模块全局**查找，挪进来 `monkeypatch` 就够不着了 |
| `summarize_agent_result` / status 口径 | 只有 Agent 链有 status；`plan_execute` 根本没有 |
| `session_key` 拼身份 · 原 `thread_id` 回显 | 端点特有（**两条轴**：预算用原值、图用拼过的，别合并） |
| 检索 / prompt 构建 / LLM 单例 | 各链专有 |
| **`[DONE]` 与其它收尾帧的先后** | 那是**每条链自己的契约**（RAG 是 `内容 → [DONE] → sources`，很反直觉但是现状）⇒ 整条尾巴交给 `on_complete`，**骨架不自动补 `[DONE]`** |
| **节点名** | 见 `graph_message_text(..., nodes=...)` —— 四条链的节点名不同，别在这里写死 |

---

## 调用方必须知道的两件事

1. **`extract` 的入参形状取决于图的 `subgraphs` 开关**（实测）：
   `subgraphs=False` ⇒ `(chunk, meta)`；`subgraphs=True` ⇒ `(namespace, (chunk, meta))`。
   `graph_message_text` 已经把这个归一化掉了，别在端点里再解一次。
2. **聚合与发帧是两件事**：本模块只管**发帧**。节点内部要把流**聚合成一条完整消息**去记账时，
   **必须对【所有】块做 `+`，不许跳过 `content` 为空的块** ——
   实测本仓 provider 把 `usage_metadata` 挂在**最后一块**（`content=''`）上，跳过它**账就没了**
   （判据见 `qingyun-rag-agent-TODO待办/探针-流式与记账.py`）。
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Callable, Collection, Optional

#  （2026-10-03 · 真服务实测）：关上游的流要用它护住（约束②）。
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

    **`ensure_ascii` 是个"为了不改线上字节"而存在的参数**，不是给人调的旋钮：

    * `False`（**默认**）= 中文原样出（UTF-8）。`/agent/*` 四条链一直是这样。
    * `True` = 中文变 `\\uXXXX`。**`/rag/stream_search` 一直是这样**（它那三帧都没传这个参数）。

    ⇒ **抽公共层不是顺手改行为的理由**：RAG 传 `True`，把旧字节**逐字留住**。
      两种编码 **JSON 解码后的值完全相同**（前端都走 `JSON.parse`）⇒ 统一与否**不影响功能**，
      但**会让"逐帧等价"这句话从"字面为真"变成"差不多"** ⇒ 本轮**不统一**，已登记待裁。
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
    # **先看长度**：`ns` 本身就是个 tuple，所以"首元素是 tuple"**分不出**前两种
    #    （`isinstance(item[0], tuple)` 对它们**都为真**）—— 顺序写反 ⇒ 把 3 元组解成 2 个 ⇒ ValueError。
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
        # **顺序 = 2026-10-03 真服务实测改的**（约束①）：
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
