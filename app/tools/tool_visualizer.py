"""
工具调用可视化模块
记录和展示 Agent 工具调用的完整过程。

**2026-10-03（N4）：键从「裸 `thread_id`」改成「拼了身份」**。
   改之前有三处跨用户可见（见  的 N4）：`get_all_traces` 谁都给、
   `get_trace` 不判属主、且 `thread_id` 默认就是 `"default"` ⇒ **后问的盖先问的**。

**两条轴别混**：本模块是**追踪**轴（进程内存）。
   `token_tracker.check_session_token_budget(user_name, thread_id)` 是**成本**轴 ——
   它**本来就带 `user_name`**，没这个病，**别去"顺手统一"它**。

**身份是必填形参、没有默认值** —— 与 `session_key`（决策 8-1）同一取向：
   **漏传要崩（`TypeError`），不是悄悄记到公共桶里。**
"""
import time
from typing import Dict, List
from dataclasses import dataclass, field

# 身份键走**全仓唯一**那一份（决策 8-1 `app/access/session_key.py`）——
#    别在本文件另拼一个 `f"{user}:{thread}"`（那个有歧义，见该模块 docstring）。
from access.session_key import session_key

@dataclass
class ToolCallRecord:
    """单次工具调用的完整记录"""
    tool_name: str
    arguments: dict
    result: str
    start_time: float
    end_time: float
    status: str = "success"  # success / error
    error_message: str = None

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result[:500] if self.result else "",
            "duration_ms": round((self.end_time - self.start_time) * 1000, 2),
            "status": self.status,
            "error_message": self.error_message,
        }

@dataclass
class AgentTrace:
    """一次 Agent 任务的完整执行轨迹"""
    thread_id: str          # **原值** —— 响应里回显的就是它（不是存储键）
    user_name: str          # **属主** —— 读侧按它过滤
    user_query: str
    start_time: float
    end_time: float = None
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    agent_decisions: List[dict] = field(default_factory=list)
    final_output: str = ""
    total_tokens: int = 0
    total_cost: float = 0.0

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "user_query": self.user_query,
            "duration_ms": round(((self.end_time or time.time()) - self.start_time) * 1000, 2),
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "agent_decisions": self.agent_decisions,
            "final_output": self.final_output[:1000] if self.final_output else "",
            "total_tokens": self.total_tokens,
            "total_cost": round(self.total_cost, 6),
        }

# 全局轨迹存储（键 = `session_key(user_name, thread_id)`，不是裸 thread_id）
_traces: Dict[str, AgentTrace] = {}

def start_trace(user_name: str, thread_id: str, user_query: str):
    """开始一次新的 Agent 任务轨迹"""
    _traces[_key(user_name, thread_id)] = AgentTrace(
        thread_id=thread_id,
        user_name=user_name,
        user_query=user_query,
        start_time=time.time()
    )

def record_tool_start(tool_name: str, arguments: dict, user_name: str, thread_id: str):
    """记录工具调用开始（可在工具实际调用前调用）"""
    key = _key(user_name, thread_id)
    if key not in _traces:
        return
    record = ToolCallRecord(
        tool_name=tool_name,
        arguments=arguments,
        result="",
        start_time=time.time(),
        end_time=0.0,
        status="running"
    )
    _traces[key].tool_calls.append(record)

def record_tool_end(tool_name: str, result: str, user_name: str, thread_id: str, status: str = "success", error_message: str = None):
    """记录工具调用结束"""
    key = _key(user_name, thread_id)
    if key not in _traces:
        return
    # 找到最近一次同名的 running 状态的调用并更新
    trace = _traces[key]
    for tc in reversed(trace.tool_calls):
        if tc.tool_name == tool_name and tc.status == "running":
            tc.result = result
            tc.end_time = time.time()
            tc.status = status
            tc.error_message = error_message
            break

def record_agent_decision(user_name: str, thread_id: str, decision: dict):
    """记录 Agent 的决策过程"""
    key = _key(user_name, thread_id)
    if key not in _traces:
        return
    _traces[key].agent_decisions.append(decision)

def finish_trace(user_name: str, thread_id: str, final_output: str = "", total_tokens: int = 0, total_cost: float = 0.0):
    """结束一次 Agent 任务轨迹"""
    key = _key(user_name, thread_id)
    if key not in _traces:
        return
    trace = _traces[key]
    trace.end_time = time.time()
    trace.final_output = final_output
    trace.total_tokens = total_tokens
    trace.total_cost = total_cost

def get_trace(user_name: str, thread_id: str) -> dict:
    """取**本人**那条轨迹。别人的 ⇒ `None`（不返回、也不说"存在但不属于你"）。"""
    trace = _traces.get(_key(user_name, thread_id))
    return trace.to_dict() if trace else None

def get_all_traces(user_name: str, *, include_all: bool = False) -> List[dict]:
    """轨迹摘要列表。

    **默认只给本人的**；`include_all=True`（**admin 例外**）才返回所有人的。
    **那个例外由调用方显式给**（`api_v1_agent.agent_trace_list` 里就一行），
       本模块**不认识角色** —— 它不 import `permission`。
    """
    items = _traces.values() if include_all else [
        t for t in _traces.values() if t.user_name == user_name
    ]
    return [
        {
            "thread_id": t.thread_id,      # **原值**（不是存储键 —— 键是拼过身份的）
            "user_query": t.user_query[:100],
            "tool_calls_count": len(t.tool_calls),
            "duration_ms": round(((t.end_time or time.time()) - t.start_time) * 1000, 2),
            "total_cost": round(t.total_cost, 6),
        }
        for t in items
    ]

def get_trace_of_any_owner(thread_id: str) -> dict:
    """按**原值** `thread_id` 找轨迹，**不看属主**。

    **这是 admin 例外用的**（决策 2：读侧给 admin 例外，但**写成显式一行**）
    ⇒ **别在普通路径上调它** —— 普通路径走 `get_trace(user_name, thread_id)`。

    为什么需要它：`/agent/trace/{thread_id}` 只收到原值，**不知道属主**；
       admin 要查别人的，就只能按原值反查（同一个 `thread_id` 命中多条 ⇒ 返回第一个，
       这是**排查用的旁路**，不承担"精确归属"的语义）。
    """
    for t in _traces.values():
        if t.thread_id == thread_id:
            return t.to_dict()
    return None

def _key(user_name: str, thread_id: str) -> str:
    """本模块的存储键 —— **唯一入口**，别在别处另拼。"""
    return session_key(user_name, thread_id)