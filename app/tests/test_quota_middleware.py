""" · 决策一落地 —— 撤掉「请求次数」配额，原位置换成 token 口径（= `R1.3`）。

## 背景（2026-10-03）

本仓原有**两套互不知情的额度口径**（实测**差 35 倍**）：

  · 请求**次数** —— `permission.ROLE_QUOTA` + `quota_limiter`，挂在 `QuotaMiddleware` 上；
  · **token** —— `token_config.ROLE_DAILY_TOKEN` + `token_tracker`。

需求方按  裁「**统一到 token 一套**」，本文件钉住这次落地。

## 为什么是「原位置换」而不是「直接撤」

`QuotaMiddleware` 的次数配额是当时**唯一**覆盖「**所有**非公开路径 + 按用户 + 按天」的一层：
  · 会话级  按 **(用户, 会话)** 计 —— 换个 `thread_id` 就重置；
  · 全局日级  是**全站合计** —— 看不到「某一个人」；
  · `check_token_budget`（按用户按天）只接了 3 处，**不是全路径**。

⇒ 直接撤会开一个「**单用户跨会话无限花**」的洞。所以撤旧的同时**就地**接上
「按用户按天的 token 上限」，**覆盖范围不变** —— 这就是 `R1.3`。

本文件**不需要 DB、不需要 Redis** —— 判据全是「纯函数」与「源码文本」。
"""
import importlib.util
import pathlib


# ===========================================================================
# ① 旧机制必须真的被撤掉（否则「两套并存」根本没解决）
# ===========================================================================

def test_role_quota_is_gone_from_permission():
    """`permission.ROLE_QUOTA` / `get_user_quota` 必须不存在 —— 它们就是「每日请求次数」那套。

    守卫意义：只要它还在，「到底哪套口径在拦」就还是个没人答得上来的问题
    —— 这正是  /  要解决的。
    """
    import access.permission as permission

    assert not hasattr(permission, "ROLE_QUOTA"), (
        "ROLE_QUOTA 又回来了 —— 本仓已裁『统一到 token 一套』（→）"
    )
    assert not hasattr(permission, "get_user_quota"), (
        "get_user_quota 又回来了 —— 它是次数配额的入口（ROLE_QUOTA 的读法）"
    )
    # 反向防「删过头」：角色体系本身还要用（api_v1_rag / permission 内部）
    assert hasattr(permission, "UserRole") and hasattr(permission, "get_user_role"), (
        "UserRole / get_user_role 被一起删掉了 —— 它们不属于次数配额，别误删"
    )


def test_quota_limiter_module_is_gone():
    """`quota_limiter` 模块必须整个不存在 —— 撤掉调用点后它**零调用者**。"""
    assert importlib.util.find_spec("quota_limiter") is None, (
        "quota_limiter 模块还在 —— 它是次数配额那套的 Redis 计数器，已无调用者"
    )


_REMOVED_NAMES = {"ROLE_QUOTA", "get_user_quota", "quota_limiter"}


def _find_references(source: str) -> list:
    """从一段源码里找出对【已删除名字】的**引用**。

    **AST 级，注释与 docstring 不算** —— 见下方自证用例。
    """
    import ast

    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in _REMOVED_NAMES:
            hits.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _REMOVED_NAMES:
            hits.append(f"line {node.lineno}: .{node.attr}")
        # `import` 走的是 `ast.alias`，**不是 `ast.Name`** ——
        #    漏掉这一支会让「最典型的引用形态」完全测不出来。
        #    （本条由 `test_reference_scanner_catches_code_but_not_comments` 抓到。）
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in _REMOVED_NAMES:
            hits.append(f"line {node.lineno}: import {node.name}")
    return hits


def test_reference_scanner_catches_code_but_not_comments():
    """**自证有效性**：扫描器对「代码引用」要报，对「注释 / 文档串」**不许**报。

    没有这条，「上面那条守卫到底测不测得出东西」就只是我的声称。
    而它**第一版真的写错过** —— 判据用的是文本匹配 `"ROLE_QUOTA" in 源码`，
    结果把 `token_config.py` / `permission.py` 里**正当的历史注释**也判成违规
    （那些注释写的正是「这里曾经有什么、为什么删」）。
    ⇒ 这就是本仓反复记的「**判据的实现与意图不一致**」：它不报错，只是**判错**。
    """
    # 必须【报】
    assert _find_references("x = ROLE_QUOTA[UserRole.FREE]"), "下标引用没被抓到"
    assert _find_references("from permission import get_user_quota"), "import 没被抓到"
    assert _find_references("quota_limiter.increment_and_check(u, n)"), "属性引用没被抓到"

    # 必须【不报】—— 这些是历史说明，正是要留的
    assert not _find_references("# 此处原有 ROLE_QUOTA，2026-10-03 删"), "注释被误判成引用"
    assert not _find_references('"""说明：quota_limiter 模块已删。"""'), "docstring 被误判成引用"


def test_no_production_module_references_role_quota():
    """`app/` 的**生产**模块里不许再**引用** `ROLE_QUOTA` / `get_user_quota` / `quota_limiter`。

    要禁的是**引用**（会让程序去找一张不存在的表），不是**提及**（说明历史）。
    """
    api_dir = pathlib.Path(__file__).resolve().parents[1]   # app/ —— tests/ 的上层
    offenders = [
        f"{path.name} {hit}"
        for path in sorted(_product_py(api_dir))
        if not path.name.startswith("test_")
        for hit in _find_references(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"这些生产模块还在【引用】已删除的名字: {offenders}"


# ===========================================================================
# ② 新机制：按用户按天的 token 上限（纯函数 ⇒ 不连 DB/Redis 也能测）
# ===========================================================================

def _payload(info: dict):
    from main import quota_reject_payload

    return quota_reject_payload(info)


def test_rejects_when_daily_token_budget_exhausted():
    """用尽（remaining == 0）⇒ 必须拒绝，且是 429 + QUOTA_EXCEEDED。"""
    from core.exceptions import ErrorCode

    body = _payload({"daily_budget": 10_000, "used_today": 10_000, "remaining": 0})

    assert body is not None, "预算已用尽却没拒绝 —— 那这一层等于不存在"
    assert body["status_code"] == 429
    assert body["code"] == ErrorCode.QUOTA_EXCEEDED.value
    # 文案必须是 **token 口径**，不许再说「调用次数」
    assert "次数" not in body["error"], (
        f"文案还停留在『次数』口径，会让调用方以为限额是次数: {body['error']!r}"
    )


def test_rejects_when_over_budget():
    """超额（remaining < 0）⇒ 也要拒绝 —— 不能只挡"刚好等于"。"""
    body = _payload({"daily_budget": 10_000, "used_today": 12_000, "remaining": -2_000})

    assert body is not None, "已超额却没拒绝"


def test_allows_when_budget_remains():
    """还有余额 ⇒ 放行（返回 None）。"""
    assert _payload({"daily_budget": 10_000, "used_today": 1, "remaining": 9_999}) is None


def test_allows_unlimited_budget():
    """`remaining` 为「无限」（哨兵）⇒ 放行 —— `get_token_budget_info` 对 `inf` 预算这么返回。"""
    assert _payload({"daily_budget": "无限", "used_today": 1, "remaining": "无限"}) is None


# ===========================================================================
# ③ 自证：把【旧】次数口径的输入喂进来，判定必须判它「不构成拒绝」
# ===========================================================================
#
# 没有这条，「上面那几条到底能不能测出『口径换没换』」就只是我的声称：
# 旧口径下 FREE 用户的限额是**100 次**，而新口径看的是 token —— 同一个用户
# 「次数没超」完全可能「token 已超」。若实现里还残留次数比较，这条会红。

def test_old_request_count_semantics_no_longer_decides():
    """旧口径的「还剩 100 次」在新实现里**不再有任何意义**。

    构造：`remaining`（token）= 0（已用尽），但"次数"完全没用完
    ⇒ 新实现**必须照样拒绝**。若它还去看次数，就会放行 ⇒ 本测试红。
    """
    body = _payload({"daily_budget": 10_000, "used_today": 10_000, "remaining": 0})

    assert body is not None, (
        "token 已用尽却被放行 —— 说明判定里还残留着『按次数』那条老路"
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
