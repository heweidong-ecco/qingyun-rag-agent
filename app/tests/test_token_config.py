"""额度常量的【唯一来源】的回归测试（对应 B7）。

本文件不碰 DB / Redis —— 只读常量。
"""
import billing.token_config as token_config


def test_single_call_caps_are_the_agreed_values():
    """作者 2026-09-30 裁的起始值。终值等前端做完、浏览器实测后再调。"""
    assert token_config.MAX_TOKENS_ANSWER == 2000
    assert token_config.MAX_TOKENS_AGENT == 1024


def test_admin_is_no_longer_infinite():
    """作者 2026-09-30 裁：「admin 也要同样上限」⇒ 不许是 inf。

    **2026-10-11靶子改过**：日额度按【池】分成两张表
    这条**对两张表各判一遍**（不是删断言）—— 「admin = premium」是**口径**，
    它必须**每一张表里都成立**，否则会冒出一个"admin 在某一池里比 premium 低"的怪状态。
    """
    for name, table in (("LLM", token_config.LLM_DAILY_TOKEN),
                        ("Embedding", token_config.EMBEDDING_DAILY_TOKEN)):
        assert table["admin"] != float("inf"), f"{name} 表里 admin 是 inf"
        assert table["admin"] == table["premium"], f"{name} 表里 admin ≠ premium"


def test_single_call_cap_does_not_depend_on_role():
    """单次上限【不分角色】—— 否则对 admin 等于不存在。"""
    assert not hasattr(token_config, "ROLE_MAX_TOKENS")


# ==================== 兜底额度必须是【最低档】，不是最高档（丁 · 2026-10-04） ====================

def test_every_role_has_a_registered_budget():
    """推导型守卫：`permission.UserRole` 里**每一个**角色都必须登记在 `ROLE_DAILY_TOKEN`。

    **为什么**：`get_user_token_budget` 是
    `ROLE_TOKEN_BUDGET.get(role, DEFAULT_DAILY_TOKEN_BUDGET)` ——
    **角色没登记 = 静默落进兜底**（不报错、不告警）。
    ⇒ 本条的判据是**推导的**（从 enum 推出名单），不是"记住 free/premium/admin 三个名字"
      —— 名单型守卫只钉住"当时知道的那几个"，新增角色时**它不会响**。

    **坦白一句**：本条**从建立起就是绿的**（三个角色当天都登记了）。
       它钉的是**不变量**，不是"修了某个 bug" —— 别把它读成"抓到过什么"。
    """
    from access.permission import UserRole
    # 2026-10-11：**两张表都要登记** —— 少登记一张，
     #    那个角色在**那一池**里就静默落进兜底值。
    for name, table in (("LLM_DAILY_TOKEN", token_config.LLM_DAILY_TOKEN),
                        ("EMBEDDING_DAILY_TOKEN", token_config.EMBEDDING_DAILY_TOKEN)):
        for role in UserRole:
            assert role.value in table, (
                f"🔴 角色 {role.value!r} 没有在 token_config.{name} 里登记 "
                f"⇒ 它在那一池里会静默落进兜底值 {token_config.DEFAULT_DAILY_TOKEN_BUDGET}"
            )


def test_unregistered_role_falls_back_to_the_LOWEST_tier_not_the_highest():
    """兜底额度必须 ≤ **现存最低档** —— 本仓取向是 fail-closed，这条是它的下限。

    **改前的事实**（2026-10-04 实测）：兜底值是 `100_000`，**等于 `premium`（最高档）**
    ⇒ 角色名拼错 / 新角色忘了登记，用户**静默拿到最高额度**，且**没有任何东西会响**。

    判据是**推导的**（不是把 `10_000` 写死）：`兜底 ≤ min(各档)`。
    ⇒ 将来真加了比 `free` 还便宜的一档，兜底**必须跟着降**，否则本条转红。
    """
    # 2026-10-11：两池分成两张表 ⇒ 兜底那个常量**对两个池都用**
    #    ⇒ 它必须 ≤ **两张表合起来的最低档**（只比一张会漏掉另一张里更低的档）。
    lowest = min(
        min(token_config.LLM_DAILY_TOKEN.values()),
        min(token_config.EMBEDDING_DAILY_TOKEN.values()),
    )
    assert token_config.DEFAULT_DAILY_TOKEN_BUDGET <= lowest, (
        f"🔴 未登记角色的兜底日预算 ({token_config.DEFAULT_DAILY_TOKEN_BUDGET}) "
        f"高于现存最低档 ({lowest}) ⇒ 角色名打错就静默拿到**更多**额度（fail-open）。"
    )


def test_an_unregistered_role_actually_gets_the_lowest_budget(monkeypatch):
    """走**真函数**（不只看常量）：一个**没登记**的角色 ⇒ 落兜底，且兜底 = 最低档。

    为什么必须走 `get_user_token_budget`：**常量改成 10_000、函数仍读别处**
       是另一类假完成（同 `test_rate_limiter_actually_reads_token_config` 记的那个形状）。

    为什么**不能**拿 `get_user_token_budget("__no_such_user__")` 来测：
       `get_user_role` 对未知用户名返回 `FREE` —— 而 `free` **是登记过的**
       ⇒ 那条会走**正常路径**，**根本碰不到兜底**，改前也是绿的（= 一条测不出东西的用例）。
       ⇒ 必须**强行**让取角色那步返回一个不存在的角色。

    **2026-10-11（两个额度池）靶子改过一次** —— **不是删断言，是换靶子**：
    日额度拆成两个池之后，`get_user_token_budget` 返回的是**两池之和**
    ⇒ 未登记角色的总额 = **2 × 最低档**，而老断言写的是 `== lowest`（那时只有一份额度）。
    ⇒ 现在钉**两件**（都比老的那条更硬）：
      ① **每一个池**都锚在最低档 —— 那才是 fail-closed 的**本体**；
      ② 总额 **= 各池之和**，且**严格低于**未登记角色**不该拿到**的那个（最高档的总额）。
    判据仍是**推导型**（不写死 `20000`）—— 将来改档位它自己跟着走。
    """
    import access.permission as permission
    import billing.token_tracker as token_tracker
    from billing import quota_pools as qp

    monkeypatch.setattr(permission, "get_user_role", lambda _user: "unregistered_role")
    fallback = token_config.DEFAULT_DAILY_TOKEN_BUDGET
    # 2026-10-11：两池两张表 ⇒ **逐个池**判它落的是不是那个兜底值
    #    （不是和某一个"全局最低档"比 —— 那个数现在不存在了）。
    for pool in qp.LIMITED_POOLS:
        got = qp.pool_daily_limit(pool, "unregistered_role")
        assert got == fallback, (
            f"未登记角色在 {pool} 池拿到了 {got}，兜底值是 {fallback} ⇒ 兜底没有 fail-closed"
        )

    # ② 总额 = 各池之和，且**低于最高档的总额**（否则就是静默拿更多额度）
    total = token_tracker.get_user_token_budget("anyone")
    want = sum(qp.pool_daily_limit(p, "unregistered_role") for p in qp.LIMITED_POOLS)
    top = sum(qp.pool_daily_limit(p, "premium") for p in qp.LIMITED_POOLS)
    assert total == want, f"未登记角色的总额 {total} ≠ 各池之和 {want} ⇒ 兜底与池的算法脱节了"
    assert total < top, f"未登记角色拿到了 {total}，最高档是 {top} ⇒ 静默 fail-open"


# ==================== S5 · 在用的模型必须**明确**配价 ====================

def test_models_actually_in_use_have_explicit_pricing():
    """在用的模型**不许**落到兜底价 —— 兜底价看不出来是兜底。

    `.env` 里 `LLM_MODEL_FAST` / `LLM_MODEL_CHAT` 走的是 DeepSeek
    （`docs/契约/环境变量.md:70`），而原先 `PRICING` **只有 qwen 系列**
    金额按兜底价算，**没有任何地方看得出它是兜底**。
    """
    # 从 config 取，不写死模型名 —— 换 `.env` 时这条测试跟着走
    from core.config import LLM_MODEL_FAST, LLM_MODEL_CHAT
    for name in (LLM_MODEL_FAST, LLM_MODEL_CHAT):
        assert name in token_config.MODEL_PRICING, (
            f"在用的模型 {name!r} 不在 MODEL_PRICING 里 ⇒ 金额会落到兜底价，"
            f"且看不出来。当前已登记：{sorted(token_config.MODEL_PRICING)}"
        )


def test_embedding_model_still_priced():
    """**在用的** embedding 模型必须登记（`rag/embedding_client` 按 model 记账）。

    2026-10-11 改成**推导型**（改前写死 `"text-embedding-v2"`）。
    **为什么必须改**：  的 反证 写着「把新模型那行注释掉 ⇒ 上面那条守卫必须红」——
       **实测它不红**（`test_models_actually_in_use_have_explicit_pricing` 只管
       `LLM_MODEL_FAST` / `LLM_MODEL_CHAT`，**不管 embedding**；而这条写死了旧模型名，
       对"新模型没登记"**无感**）⇒ 结论：**换 embedding 模型这件事上，一道门都没有**。
       ⇒ 改成从 `config` 取，**换 `.env` 时这条跟着走**（与上面那条 LLM 的同一种形状）。
    **B 段切模型时**：`.env` 与 `app/core/config.py` 的**字面默认值**要一起翻到新模型
       （CI 没有 `.env`，读的就是那个字面默认值）—— 否则这条会在 CI 上红，而那是**对的**。
    """
    from core.config import EMBEDDING_MODEL_MAIN
    assert EMBEDDING_MODEL_MAIN in token_config.MODEL_PRICING, (
        f"在用的 embedding 模型 {EMBEDDING_MODEL_MAIN!r} 不在 MODEL_PRICING 里 ⇒ "
        f"金额会落到兜底价，且看不出来。当前已登记：{sorted(token_config.MODEL_PRICING)}"
    )


def test_all_registered_prices_are_positive():
    """登记的单价不许是 0 或负数 —— 那会**静默把花费记成 0**。

    **本条替代了原先的 `test_default_pricing_is_not_lower_than_models_in_use`**（2026-10-01 删）。
       那条断言「兜底价 >= 在用模型价」，**在 CI 上直接红**：
       CI 没有 `.env` ⇒ `config.LLM_MODEL_CHAT` 落到**代码里的默认值**
       （**当时**是 `config.py:55` 的 `qwen-plus`；**2026-10-02 起已改成 `deepseek-v4-flash`**，
       见  —— 这条记的是**那天**的事实）
       ⇒ 0.008/0.016 **本来就高于**兜底 0.003/0.006 ⇒ `assert 0.003 >= 0.008` 失败。
    **根因不是"CI 配置特殊"，是那条不变量本身不成立** ——
       兜底价是给**未登记**模型的猜测值，**与"在用模型贵不贵"没有推导关系**；
       而在用模型**只要登记了就永远不走兜底** ⇒ 那条断言**给不出任何保护**，
       却会随部署选哪个模型而时红时绿。**这是"我发明了一条规矩"的第二次**（第一次见 commit `8a5672b`）。
    **真正防"静默低报"的是上面那条**（在用模型必须登记），不是这个兜底价。
    """
    for name, p in token_config.MODEL_PRICING.items():
        assert p["prompt"] > 0, f"{name} 的 prompt 价 <= 0 ⇒ 花费会被静默记成 0"
        assert p["completion"] >= 0, f"{name} 的 completion 价为负"
    d = token_config.DEFAULT_MODEL_PRICING
    assert d["prompt"] > 0 and d["completion"] > 0, "兜底价必须为正，否则未登记模型的花费记成 0"


# ==================== S6 · 限流参数也收进来 ====================

def test_record_usage_never_hardcodes_a_model_name():
    """`record_usage(model="…")` 不许写死字符串。

    为什么：`.env` 换模型（qwen → deepseek）时，写死的地方**不会跟着改**，
    而**金额按旧模型的单价算**，且从数字上看不出来。本仓实测栽过 3 处
    （`agent_graph_advanced.py` ×2 · `agent_checkpointer.py` ×1）。

    用 AST 而不是 grep —— 注释与 docstring 里也有同样的串（本仓栽过，
       见 `docs/规范/开发规范.md` §3.1「批量替换后按【位置】核」）。
    """
    import ast
    import pathlib
    offenders = []
    for p in sorted(_product_py(pathlib.Path(__file__).resolve().parents[1])):
        if p.name.startswith("test_"):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "record_usage":
                continue
            for kw in node.keywords:
                if kw.arg == "model" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    offenders.append(f"{p.name}:{node.lineno} → model={kw.value.value!r}")
    assert not offenders, (
        "这些 record_usage 把模型名写死了 ⇒ 换模型后金额会算错：\n  " + "\n  ".join(offenders)
    )


# ==================== B7 · 搬家是【真搬】，不是复制一份 ====================

def test_tracker_aliases_point_at_token_config():
    """别名必须**是同一个对象**，不是"值相同的新字典"。

    光比值分不出"搬了"还是"抄了一份" —— 抄的那份**下次改价时不会跟着动**，
       而这正是这个 Task 要解决的问题本身。

    **2026-10-11（两个额度池）：`ROLE_TOKEN_BUDGET` 那条【换了落点】** ——
    改成两个池之后 `get_user_token_budget` 不再读角色额度（它读 `quota_pools.total_daily_limit`）
    ⇒ 那条别名在 `token_tracker` 里**变成死导入**（`ruff` 的，被提交门当场拦下并删掉了）。
    **不是把这条检查删掉** —— 它**搬到真正读它的地方**（`billing/quota_pools.py`），
    否则"角色额度只有一处来源"这件事就**没人看着了**。
    """
    import billing.quota_pools as quota_pools
    import billing.token_tracker as token_tracker
    assert token_tracker.PRICING is token_config.MODEL_PRICING
    assert token_tracker._DEFAULT_PRICING is token_config.DEFAULT_MODEL_PRICING
    assert token_tracker.MAX_SINGLE_CALL_COST is token_config.MAX_SINGLE_CALL_COST
    assert token_tracker.MAX_THREAD_COST is token_config.MAX_THREAD_COST
    # 角色额度那两个 ⇒ 现在的读法是 `quota_pools`（每池各取一次）
    # 2026-10-11：日额度按池分成两张表 ⇒ 这里**两张都要指回同一个对象**
    #    （不是删掉这条 —— 它钉的是"没有抄一份出来"，而抄一份**下次改价不会跟着动**）。
    assert quota_pools.LLM_DAILY_TOKEN is token_config.LLM_DAILY_TOKEN
    assert quota_pools.EMBEDDING_DAILY_TOKEN is token_config.EMBEDDING_DAILY_TOKEN
    assert quota_pools.DEFAULT_DAILY_TOKEN_BUDGET is token_config.DEFAULT_DAILY_TOKEN_BUDGET


def test_rate_limiter_params_are_centralized():
    """限流参数与额度参数**同型**（都是写死）⇒ 一起收口。"""
    for attr in ("GLOBAL_LIMIT_RATE", "GLOBAL_LIMIT_CAPACITY",
                 "USER_LIMIT_RATE", "USER_LIMIT_CAPACITY"):
        assert isinstance(getattr(token_config, attr), (int, float)), attr


def test_rate_limiter_actually_reads_token_config():
    """常量搬了家、**限流器没读** = 没搬（同「B7 接线」那类假完成）。

    **光比值没用** —— 若两边都写 100.0，"读了"与"恰好同值"分不出来。
    ⇒ 改成**用环境变量把值改掉**，另起一个进程导入 ⇒ 只有真读了 `token_config` 才可能对上。
    """
    import os
    import subprocess
    import sys

    code = (
        "import billing.token_config, access.rate_limiter;"
        "assert billing.token_config.GLOBAL_LIMIT_RATE == 7.5, billing.token_config.GLOBAL_LIMIT_RATE;"
        "assert billing.token_config.USER_LIMIT_CAPACITY == 11, billing.token_config.USER_LIMIT_CAPACITY;"
        "assert access.rate_limiter.global_limiter.rate == 7.5, access.rate_limiter.global_limiter.rate;"
        "assert access.rate_limiter.user_limiter.capacity == 11, access.rate_limiter.user_limiter.capacity;"
    )
    env = {**os.environ,
           "GLOBAL_LIMIT_RATE": "7.5", "USER_LIMIT_CAPACITY": "11",
           "GRADIO_ANALYTICS_ENABLED": "False"}
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        "改环境变量后限流器没跟着变 ⇒ 它没读 token_config：\n" + r.stderr[-800:]
    )


# ===========================================================================
# 2026-10-09（模块化重构）：**产品模块的枚举口径**
# ===========================================================================
# **为什么不能再用 `api_dir.glob("*.py")`**：
#   2026-10-09 把 60 个模块按组收进了 `app/<组>/`（core · routing · access
#   billing · agent · rag · tools）⇒ **根目录下再也扫不到它们** ⇒ `glob` 返回
#   **空列表** ⇒ 下面那些「对每个模块…」的断言**全部为真**。
#   ⇒ 本仓原话：「**空跑 = 静默假通过**」（`pre-commit-gates.py` / `test_web_pages.py` 都栽过）。
#   ⇒ 所以这里改成**递归**，并且**递归之后必须排掉两处**：
#       · `tests/`  —— 测试不是产品模块
#       · `alembic/` —— 迁移脚本，**重构前就扫不到**（原来 `glob("*.py")` 只看 app/ 根）
#   ⇒ 并且带一条**防空跑断言**：真扫到 0 个 ⇒ 当场红，不许静默变绿。
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


# ==================== 额度键改名（2026-10-11） ====================
#
# 日额度按【池】分开 ⇒ 旧的 `DAILY_TOKEN_FREE` / `_PREMIUM` / `_ADMIN` **不再被读**。
# 留在 `.env` 里**不会报错**，只是额度悄悄变成别的数 —— 所以必须**点名拒绝启动**。

def test_renamed_token_keys_are_detected(monkeypatch):
    """旧键在环境里 ⇒ 必须被认出来（纯函数，不依赖别的必需项）。"""
    import core.config as config

    assert config.renamed_token_keys_present() == [], "本机环境里怎么已经有旧键了"

    monkeypatch.setenv("DAILY_TOKEN_FREE", "20000")
    assert config.renamed_token_keys_present() == ["DAILY_TOKEN_FREE"]

    monkeypatch.setenv("DAILY_TOKEN_ADMIN", "1")
    assert config.renamed_token_keys_present() == ["DAILY_TOKEN_ADMIN", "DAILY_TOKEN_FREE"]


def test_validate_config_refuses_to_start_with_a_renamed_key(monkeypatch):
    """**它是"点名报错"，不是 warning** —— warn 在容器日志里没人看，而这条错了要花钱。

    五个必需项**直接打在模块属性上**（不是 env）—— 这样本条的判据
    **只**在测"旧键拦不拦"，不会被"别的必需项缺了"干扰。
    """
    import pytest
    import core.config as config

    for k in ("DASHSCOPE_API_KEY", "LLM_API_KEY", "POSTGRES_PASSWORD",
              "JWT_SECRET_KEY", "LOGIN_PASSWORD"):
        monkeypatch.setattr(config, k, "dummy")

    config.validate_config()          # 前置：没有旧键时**不许**抛（否则下面那条是假绿）

    monkeypatch.setenv("DAILY_TOKEN_FREE", "20000")
    with pytest.raises(EnvironmentError, match="DAILY_TOKEN_FREE"):
        config.validate_config()
