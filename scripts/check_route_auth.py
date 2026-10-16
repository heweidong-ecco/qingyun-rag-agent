#!/usr/bin/env python3
"""列出【没有鉴权依赖】的路由 —— 并和基线比（B 类检查）。

## 为什么要有这个脚本（2026-09-30）

**起因**：核 `api_v1.py` 时手工扫"哪些路由没鉴权"，**第一版扫出 0 条** ——
因为 `FastAPI 0.141` 起 `include_router` 的结果被包成 `_IncludedRouter`，
**`len(app.routes)` 不再等于路由总数**。

**而这个坑【仓里早就写着】** —— `app/tests/test_public_paths.py:17-20` 一字不差地记着。
⇒ **知识在，但挂在一个谁都不会去读的地方**（那个测试文件的 docstring 里）。
⇒ 所以把它**做成一条能跑的命令**（本仓立场：「**写不出命令的，就是还没核过**」）。

## 用法

```bash
python3                  # 只列出（人读）
python3  --baseline      # 和基线比，多了就 exit 1
python3  --write-baseline # 把当前结果写成新基线
```

**它不 import 网络/DB** —— 但会 import `main`（那会建 Gradio Blocks、要 `.env`）。
   实测耗时见脚本末尾的 `--selftest`。

**判据的口径**：`MIDDLEWARE_EXEMPT_PATHS` 里的**跳过不报** —— 但**那是"跳过两个中间件"的意思，
   不是"该端点公开"**（`/api/v1/admin/create_user` 就在名单里，可它要 `require_admin`）。
   —— **只报"不在豁免名单、又没鉴权依赖"的**。

**2026-10-05起它也扫 WebSocket** —— 此前只认 `APIRoute`，
   `/api/v1/ws/agent`（**真花钱、真没鉴权**）**一条都扫不到**。
   那条盲区  记在文字里，但**文字不是门**；现在它进了这份清单。
   WS 行的标签是 `WS`（WS 没有 HTTP 动词，标 `GET` 会误导）。
"""
import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = REPO / "app"
BASELINE = REPO / "scripts" / "route-auth-baseline.txt"

# 这些依赖名 = 「这条路由要身份」。
# 加新依赖时**记得加进这里**，否则会误报成"无鉴权"。
AUTH_NAMES = {
    "get_current_user",          # API Key
    "get_current_user_hybrid",   # API Key 或 JWT
    "get_current_user_jwt",      # JWT
    "require_admin",             # 管理员
    "check_budget",              # 预算检查（它内部就依赖身份）
    "require_ws_user",           # WebSocket 首帧认证
}


def _dep_names(dependant) -> set:
    """递归取一个 dependant 子树里所有依赖的函数名。"""
    out = set()
    for sub in getattr(dependant, "dependencies", []) or []:
        call = getattr(sub, "call", None)
        if call is not None:
            out.add(getattr(call, "__name__", str(call)))
        out |= _dep_names(sub)
    return out


def _collect(routes, http: list, ws: list) -> None:
    """本脚本存在的一半理由：**必须递归进 `_IncludedRouter`**。

    直接遍历 `app.routes` 会漏掉所有 `include_router` 进来的路由 ——
       在本仓那是 **53 条里的 48 条**（实测：不递归只能看到 5 条）。

    2026-10-05加第二个出口 `ws`：**WebSocket 也要收**。
       改动前这里只认 `APIRoute` ⇒ `/api/v1/ws/agent`（**真花钱、真没鉴权**）
       **一条都扫不到**。那条盲区  记在文字里，但没做成门。
       两个出口都用 `list.append`（就地改），别改成"返回新列表"。
    """
    for r in routes:
        kind = type(r).__name__
        if kind == "_IncludedRouter":
            # 属性名是 `original_router`（不是 `.routes`/`.router`）—— 实测出来的
            _collect(getattr(r.original_router, "routes", []) or [], http, ws)
        elif kind == "APIRoute":
            http.append(r)
        elif kind == "APIWebSocketRoute":
            ws.append(r)


def find_routes_without_auth(routes, exempt_paths) -> tuple[list[tuple[str, str]], int, int]:
    """**纯函数**那一半（不碰 cwd / 不 import `main`）—— 供 `scan` 与 pytest 共用。

    返回 `(无鉴权路由列表, HTTP 路由数, WS 路由数)`，列表元素是 `(方法, 路径)`。
    WS 行的方法标签是 `"WS"`（WS 没有 HTTP 动词，标 `"GET"` 会误导）。
    **本函数不许 `os.chdir`** —— 它会被 pytest 进程直接调用。
    """
    http, ws = [], []
    _collect(routes, http, ws)

    no_auth = []
    for r in http + ws:
        if r.path in exempt_paths:
            continue
        if _dep_names(r.dependant) & AUTH_NAMES:
            continue
        if getattr(r, "methods", None):
            methods = sorted(set(r.methods) - {"HEAD", "OPTIONS"})
            label = methods[0] if methods else "?"
        else:
            label = "WS"
        no_auth.append((label, r.path))
    return sorted(no_auth, key=lambda x: x[1]), len(http), len(ws)


def scan() -> tuple[list[tuple[str, str]], int]:
    """返回 (无鉴权路由列表, 总路由数)。"""
    sys.path.insert(0, str(API))
    os.chdir(API)                      # `main` 的导入期要读相对路径的 .env

    # 必须在 `import main` **之前**设 —— 同 `app/conftest.py:20` 的理由：
    #    Gradio 在**导入期**就起非 daemon 线程去连 huggingface.co 发匿名遥测，
    #    本机网络不通时**卡在 TCP connect 上** ⇒ 实测把本脚本从 ~3s 拖到 **91s**。
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    import warnings
    warnings.filterwarnings("ignore")

    from main import app, MIDDLEWARE_EXEMPT_PATHS      # noqa: E402

    no_auth, n_http, n_ws = find_routes_without_auth(app.routes, MIDDLEWARE_EXEMPT_PATHS)
    return no_auth, n_http + n_ws


def _read_baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    return {
        ln.strip()
        for ln in BASELINE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="和基线比；变多则 exit 1")
    ap.add_argument("--write-baseline", action="store_true", help="写成新基线")
    args = ap.parse_args()

    rows, total = scan()
    paths = {p for _, p in rows}

    if args.write_baseline:
        BASELINE.write_text(
            "# 无鉴权路由基线（--write-baseline 生成）\n"
            "# 每一行 = 一条「不在 MIDDLEWARE_EXEMPT_PATHS、且没有鉴权依赖」的路由。\n"
            "# ⚠️ 这份基线【不是「允许清单」】—— 它是「已知的债」，见  🅗。\n"
            "# 🔴 2026-10-04后本表只剩 /api/v1/（main.py 根路径，一个 ping）；\n"
            "#    表里原先指的 S1/S2/S14 三条【已闭合】⇒ ⛔ 别再去表里找它们。\n"
            "#    这一条的建议是【留公开】，但仍未正式裁定 ⇒ 见 app/routing/specs/api_v1.md 末节。\n"
            "# 🔴 2026-10-05本表**开始含 WebSocket** —— 原先这里只有 HTTP，\n"
            "#    于是 /api/v1/ws/agent（真花钱、真没鉴权）【一条都扫不到】。当时补上后为 3 条。\n"
            "# 🟢 2026-10-05那条真花钱的 WS 已【真修好】（不是挪走）：\n"
            "#      /api/v1/ws/agent —— 首帧认证（deps.require_ws_user）；顺带把身份透传给记账，\n"
            "#                          账不再记在写死的 \"unknown\" 头上\n"
            "# ⚰️ 同日（§十）另一条 WS `/api/v1/ws/test` **已删** —— 纯回声的测试桩，\n"
            "#    消费者 = 0（本仓 / 本仓测试 / 5 个仓外项目，三处独立扫过）；且首帧认证补上后，\n"
            "#    它连「免鉴权的探活口子」这个唯一可能的用途也没了。\n"
            "#    ⇒ 现为 **1 条**，且这唯一一条是 **HTTP**（⛔ 本表现在一条 WS 都不含）。\n"
            "#    ⛔ 那条 WS **别再加回这里** —— 加回来等于把它重新变成匿名可达。\n"
            "# 🟢 2026-10-06新增 1 条：/chat —— **对话页的入口 URL**，故意公开\n"
            "#    （它是给人打开 HTML 的跳转，本身不含数据；真正的边界在它调的后端接口上）。\n"
            "# ⛔ 变多 = 新引入了没鉴权的路由 ⇒ 该拦；变少 = 修好了 ⇒ 重新生成基线。\n"
            + "\n".join(sorted(paths)) + "\n",
            encoding="utf-8",
        )
        print(f"✅ 基线已写入 {BASELINE.relative_to(REPO)}（{len(paths)} 条）")
        return 0

    n_http = sum(1 for m, _ in rows if m != "WS")
    n_ws = len(rows) - n_http
    print(f"真实路由总数：{total}（HTTP + WS；含 include_router 进来的 —— 已递归进 _IncludedRouter）")
    print(f"🔴 无鉴权路由：{len(rows)} 条（HTTP {n_http} · WS {n_ws}）\n")
    for m, p in rows:
        print(f"   {m:6} {p}")

    if not args.baseline:
        return 0

    base = _read_baseline()
    new = paths - base
    gone = base - paths
    if new:
        print(f"\n❌ 比基线【多了 {len(new)} 条】—— 新引入了没鉴权的路由：")
        for p in sorted(new):
            print(f"     + {p}")
        print("\n⇒ 要么给它加 `Depends(get_current_user_hybrid)`（或 require_admin），")
        print("   要么确认它确实该无鉴权 —— 后者请跑 `--write-baseline` 把它写进基线并注明理由。")
        print("   ⚠️ 对 `WS` 行：**加进 `MIDDLEWARE_EXEMPT_PATHS` 没用** ——")
        print("      中间件本来就管不到 WebSocket，WS 的拒绝只能在处理函数里做（帧或 close code）。")
        return 1
    if gone:
        print(f"\n✅ 比基线少了 {len(gone)} 条（修好了）：")
        for p in sorted(gone):
            print(f"     - {p}")
        print("   ⇒ 跑 `--write-baseline` 更新基线。")
        return 0
    print("\n✅ 与基线一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
