# websocket_callback.py
import json
from typing import Any, Dict, List
from langchain_core.callbacks import BaseCallbackHandler
from fastapi import WebSocket

from billing.token_tracker import record_from_response


class WebSocketAgentCallback(BaseCallbackHandler):
    r"""将 Agent 的中间过程实时推送到 WebSocket，**并给这一轮 LLM 调用记账**。

    ## 为什么要记账

    `/api/v1/ws/agent` 是**真花钱**的一条链，但它的账**一笔都没写过**
    （`grep -n 'record_from_response\|record_usage' app/routing/api_v1_rag.py` ⇒ 零命中）。
    而它身上**已经挂着** `check_session_token_budget` —— 那条闸的数据源是
    `token_usage_logs` 里 `(user_name, thread_id)` 的今日累计。
    **没人写 ⇒ 每次都读到 0 ⇒ 闸永远放行** = 摆设
    （正是  §六 备选 B 明文否掉的形态）。

    ## 身份**必填**，不给默认值

    漏传 = `TypeError`（**响亮**），而不是静默记成 `"unknown"`（**假记账**）——
    与  §四 对 `query_rewriter` 的处理同一条取向。
    """

    def __init__(self, websocket: WebSocket, *, user_name: str, thread_id: str, llm: Any):
        self.websocket = websocket
        self.user_name = user_name
        self.thread_id = thread_id
        # 记账要用它的 `model_name` —— 按**真实模型**计价，不许写死
        #    （：本仓因写死 `"qwen-turbo"` 按错的单价记过账，有两处前科）。
        self.llm = llm

    async def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> None:
        """LLM 开始思考时触发"""
        pass  # 可以选择发送一个“思考中”的状态

    async def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """工具开始执行时触发"""
        tool_name = serialized.get("name", "unknown")
        await self.websocket.send_text(json.dumps({
            "type": "action",
            "tool": tool_name,
            "content": f"调用工具：{tool_name}"
        }))

    async def on_tool_end(self, output: str, **kwargs: Any) -> None:
        """工具执行完成时触发"""
        await self.websocket.send_text(json.dumps({
            "type": "observation",
            "content": str(output)[:300]
        }))

    async def on_llm_end(self, response, **kwargs: Any) -> None:
        """LLM 生成完成时触发 —— **在这里记账**（每一轮 LLM 调用各记一笔）。

        `record_from_response` 的判据是消息上的 **`usage_metadata`**，
           不是 `.usage`（那个属性**不存在**，本仓因此有过"记账从写下那天起就没执行过"的前科，
           墓碑见 `app/tests/test_token_budget_hookup.py`）。
        取不到用量时它返回 `False` 并**静默跳过** —— 有意如此：
           **不许估一个数记上去**，账本里写假数比漏记更坏（**无法与真数区分**）。
        """
        for generations in getattr(response, "generations", None) or []:
            for gen in generations or []:
                message = getattr(gen, "message", None)
                if message is None:
                    continue
                record_from_response(
                    self.llm, message, "agent_decision",
                    user_name=self.user_name, thread_id=self.thread_id,
                )

    async def on_agent_action(self, action, **kwargs: Any) -> None:
        """Agent 决定采取行动时触发"""
        await self.websocket.send_text(json.dumps({
            "type": "action",
            "tool": action.tool,
            "input": str(action.tool_input)[:200],
            "content": f"决定调用：{action.tool}"
        }))

    async def on_agent_finish(self, finish, **kwargs: Any) -> None:
        """Agent 得出最终答案时触发"""
        await self.websocket.send_text(json.dumps({
            "type": "final",
            "content": finish.return_values.get("output", str(finish))
        }))
        await self.websocket.send_text(json.dumps({"type": "done"}))