"""手动实验脚本：对比不同约束条件下的任务规划结果。
"""
from agent.plan_execute import planner_llm
from langchain_core.messages import HumanMessage, SystemMessage

GOAL = "帮我研究Python和Go在Web开发中的优劣，并给出推荐"


def run_plan(prompt_version: str, extra_constraints: str):
    """用不同约束测试规划"""
    base_prompt = """你是一个专业的任务规划助手。你的职责是将用户的目标分解为可执行的步骤清单。

**规划规则：**
1. 每个步骤必须是一个具体的、可执行的操作。
2. 步骤之间必须有清晰的逻辑顺序，不能跳跃。
3. 如果某个步骤依赖前面的结果，必须在描述中明确说明。
4. 每个步骤需要指定使用的工具（tool）和输入（input）。
5. 可用的工具包括：web_search（搜索）、calculator（计算）、date_today（日期）、execute_python（代码执行）。

**输出格式（严格JSON数组）：**
[
    {"step": 1, "action": "操作描述", "tool": "工具名", "input": "工具输入"},
    {"step": 2, "action": "操作描述", "tool": "工具名", "input": "工具输入"}
]

请严格按照JSON格式输出，不要包含任何其他文本。"""

    system_prompt = base_prompt + "\n" + extra_constraints
    
    response = planner_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"用户目标：{GOAL}")
    ])
    
    print(f"\n{'='*60}")
    print(f"测试版本：{prompt_version}")
    print(f"约束条件：{extra_constraints}")
    print(f"{'='*60}")
    print(response.content)
    
    return response.content


if __name__ == "__main__":
    # 测试1：无额外约束（基线）
    run_plan("V1-基线（无额外约束）", "")
    
    # 测试2：限制步骤数
    run_plan("V2-限制步骤数", "6. 步骤总数不超过5个。")
    
    # 测试3：优先使用搜索工具
    run_plan("V3-优先搜索", "6. 优先使用 web_search 工具获取信息，其他工具只在必要时使用。")
    
    # 测试4：组合约束
    run_plan("V4-组合约束", "6. 步骤总数不超过5个。\n7. 优先使用 web_search 工具获取信息。")
# ==========预期结果 分析和对比：=============