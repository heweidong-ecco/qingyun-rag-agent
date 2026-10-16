"""`make_llm` 收口的回归（`①b`  **+  改写**）。

为什么不能只测常量值：常量对了、**没接上**，等于没有上限。
   本文件钉的是"构造点真的走工厂、且两个轴的角色对"。
"""
import ast
import pathlib

import billing.token_config as token_config

API = pathlib.Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层

# 唯一允许直连 `ChatOpenAI(` 的文件
FACTORY = "core/llm_factory.py"


def _calls_in(fname: str, func_name: str):
    """扫 `app/<fname>`，yield 每个 `func_name(...)` 调用的 AST `Call` 节点。

    用 AST 而不是 grep：`ChatOpenAI(` 也会出现在**注释与 docstring** 里
       （本仓栽过 —— `docs/规范/开发规范.md` §3.1「批量替换后按位置核」）。
    """
    tree = ast.parse((API / fname).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (getattr(fn, "id", None) or getattr(fn, "attr", None)) == func_name:
            yield node


def _source_files():
    """`app/` 下的产品 `.py`，**返回相对 `app/` 的路径**（如 `core/llm_factory.py`）。

    ：原先返回 `p.name`（裸名）—— 重构后同名文件在不同组里会**撞名**，
       且与 `FACTORY` / `EXPECTED_ROLES` 的键**对不上**（它们现在写的是相对路径）。
    """
    return [p.relative_to(API).as_posix() for p in sorted(_product_py(API)) if not p.name.startswith("test_")]


def _raw_chatopenai_sites():
    """yield `(文件, 行号)` —— 工厂以外的裸 `ChatOpenAI(`。"""
    for fname in _source_files():
        if fname == FACTORY:
            continue
        for node in _calls_in(fname, "ChatOpenAI"):
            yield fname, node.lineno


def _make_llm_sites():
    """yield `(文件, 行号, model_role, token_role)` —— 每个 `make_llm(...)` 调用。

    两个轴都取**字面量**；取不到字面量（变量、表达式）时给 `None`，由第 2 条报错。
    """
    for fname in _source_files():
        for node in _calls_in(fname, "make_llm"):
            roles = []
            for i in range(2):
                arg = node.args[i] if len(node.args) > i else None
                roles.append(getattr(arg, "value", None) if isinstance(arg, ast.Constant) else None)
            yield fname, node.lineno, roles[0], roles[1]


# ==================== 第 1 条：工厂以外，零直连 ====================

def test_no_raw_chatopenai_outside_the_factory():
    """**`app/core/llm_factory.py` 以外，全仓不许再出现裸 `ChatOpenAI(`。**

    **为什么这条比"每处都写了 max_tokens"更强**：
    后者**默许直连**，只求每处各自写对；前者**只留一个落点**
    改 `model` / `max_tokens` / 换 provider 时，**只需要改那一个文件**。

    ⇒ 这条失败时，**失败信息本身就是待办清单**（逐行给出文件:行号）——
      把它们改成 `make_llm("<fast|chat>", "<answer|agent>", ...)`。
    """
    offenders = [f"{f}:{ln}" for f, ln in _raw_chatopenai_sites()]
    assert not offenders, (
        "这些地方还在直连 ChatOpenAI ⇒ 绕开了唯一的构造落点（改 model/max_tokens 会漏掉它们）：\n  "
        + "\n  ".join(offenders)
        + "\n请改走 `from llm_factory import make_llm` —— 角色见 app/core/specs/llm_factory.md。"
    )


# ==================== 第 2 条：两个轴的角色裁定 ====================

# 每个**含 `make_llm` 的文件**该用哪组角色。
# **这不是"从代码里推出来的规律"，是把 【改动前】的取值原样固化** ——
#     是**零行为变化**的收口，所以这张表必须等于改动前 15 处的实际取值
#    （`agent_graph.py` 那类"用 fast"的地方**不许被顺手改成 chat**）。
EXPECTED_ROLES = {
    # ---- 答案生成类：要完整答案 ⇒ answer(2000) ----
    "routing/api_v1_rag.py": ("chat", "answer"),          # get_llm_stream 流式答案 · WS agent 对外答案
    "rag/rag_pipeline.py": ("fast", "answer"),        # answer_llm（模型轴是 fast，与下面不同）
    #    它是**离线评测脚本**：生成"被评的答案"、又当 RAGAS judge ⇒ 两处都是长输出。
    #    不收敛的话会绕开工厂；接小（1024）则**judge 输出可能被截断 ⇒ 评分静默失真**。
    "eval/evaluate_with_ragas.py": ("chat", "answer"),
    # ---- 中间步骤类：planner / executor / 质检 / agent 各节点 ⇒ agent(1024) ----
    "agent/agent_checkpointer.py": ("fast", "agent"),
    "agent/agent_graph.py": ("fast", "agent"),
    "agent/agent_graph_advanced.py": ("chat", "agent"),
    "agent/agent_graph_advanced_learning.py": ("chat", "agent"),   # 主 llm + 三个子图 llm + REACT 子图
    "agent/plan_execute.py": ("chat", "agent"),                    # planner / executor / quality_checker
    #    （mem0 用自己那个 openai provider，而且**不记账**）。
    # **归「中间步骤」**：它的输出是一串**事实**，不是给用户看的答案。
    # 改前那版**没设 `max_tokens`**（吃端点默认）⇒ 接小是**行为变更**；
    #    已**端到端实测过**（`/agent/memory/add` 真跑通、事实真进了库）。
    "agent/memory_store.py": ("fast", "agent"),
}


def test_every_make_llm_call_passes_the_agreed_roles():
    """**只钉"走了工厂"不够** —— 全传 `("chat", "agent")` 也能过第 1 条，
    但那会把**答案截断**（`token_config` 里写明：压到 1800 以下就开始截答案）。

    ⇒ 第 2 条钉**分类**：每个文件用的是不是裁定里那组角色。

    **模型轴也一起钉**：`rag_pipeline` / `agent_graph*` / `agent_checkpointer` 走 `fast`，
       其余走 `chat` —— 这是**改动前的实际取值**，不是"看着像"。
       顺手改模型轴 = **改行为 + 改账单**，那不属于 （收口）的范围。
    """
    import core.llm_factory as llm_factory

    known_models = set(llm_factory._MODEL_ROLE_TO_KEY)
    known_tokens = set(llm_factory._TOKEN_ROLE_TO_CONST)

    seen, wrong = set(), []
    for fname, lineno, model_role, token_role in _make_llm_sites():
        seen.add(fname)
        got = (model_role, token_role)
        want = EXPECTED_ROLES.get(fname)
        if model_role not in known_models or token_role not in known_tokens:
            wrong.append(
                f"{fname}:{lineno} 的角色 {got} 取不到合法字面量"
                f" ⇒ 只认 model_role={sorted(known_models)} / token_role={sorted(known_tokens)}"
                "（⚠️ 也别写成变量：那会让本门禁失明）"
            )
        elif want is None:
            wrong.append(f"{fname}:{lineno} 用了 {got}，但本文件【没进裁定表】"
                         f" ⇒ 请先决定它属于「答案」还是「中间步骤」")
        elif got != want:
            wrong.append(f"{fname}:{lineno} 用的是 {got}，裁定表要求 {want}")
    assert not wrong, "角色与裁定表不符：\n  " + "\n  ".join(wrong)

    # 反向：裁定表里列了、但代码里已经找不到调用点的 —— 表腐了，要清
    stale = sorted(set(EXPECTED_ROLES) - seen)
    assert not stale, f"裁定表里的这些文件已无 make_llm 调用点，请从表中删掉：{stale}"


def test_the_two_budgets_are_distinct_and_answer_is_larger():
    """两个常量**不许相等** —— 相等意味着"分类"这件事被做没了（一处改两处一起变，看不出来）。"""
    assert token_config.MAX_TOKENS_ANSWER > token_config.MAX_TOKENS_AGENT, (
        "答案上限必须大于中间步骤上限：答案被截断是用户可见的质量事故，"
        "中间步骤被截断只是多跑一轮"
    )


# ===========================================================================
# ===========================================================================
# **为什么不能再用 `api_dir.glob("*.py")`**：
#   billing · agent · rag · tools）⇒ **根目录下再也扫不到它们** ⇒ `glob` 返回
def _product_py(api_dir):
    """`app/` 下的**产品 .py**（`app/` 根 + 七个模块组）。"""
    ps = [p for p in api_dir.rglob("*.py")
          if "__pycache__" not in p.parts
          and "tests" not in p.parts
          and "alembic" not in p.parts
          and not p.name.startswith("test_")]
    assert ps, f"🔴 防空跑：{api_dir} 下扫到 0 个产品模块 —— 枚举口径又变了"
    return sorted(ps)


def _mod_name(api_dir, path):
    """模块的**可导入名** —— 相对 `app/` 的点分路径（如 `agent.agent_graph`）。

    用它是因为 `importlib.import_module("agent_graph")` 在重构后**会 ModuleNotFoundError**
       （真模块名已是 `agent.agent_graph`）。
    """
    return path.relative_to(api_dir).with_suffix("").as_posix().replace("/", ".")
