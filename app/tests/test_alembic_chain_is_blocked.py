""" · **alembic 那条链在真库上【跑不动】** —— 结构性拦截（2026-10-16 加）。

## 为什么要有它

`app/alembic/versions/f12a761ae626_init.py` 的 `upgrade()` 里有一句
`op.drop_column('documents', 'requested_by')` —— 那是**多用户隔离的承重列**
⇒ **跑一次就把隔离拆了，而且不报错**（2026-10-10 真跑离线模式才看见的）。

🔴 **而"别跑"此前只写在注释与三份文档里 —— 没有任何执行体。**
本仓立场（`CLAUDE.md` §六）：**只有文字就漏，结构才执行。**
⇒ `app/alembic/env.py` 现在对**在线模式**（真连库）直接抛。

## 本文件钉三条

| # | 钉什么 | 不钉会怎样 |
|---|---|---|
| 1 | **在线**（`alembic upgrade head`）**被拒绝**，且抛的是**我们那一句**、⛔ 不是连接报错 | 将来有人把那个 `raise` 换回 `run_migrations_online()` ⇒ **没有任何东西会响**，那把枪又装上子弹 |
| 2 | **离线**（`alembic upgrade head --sql`）**照旧可用** | ⚠️ 拦过头会把 `app/alembic/CLAUDE.md` 里写着的**既有判据**一起弄坏 |
| 3 | `alembic history` 照旧可用 | 同上 |

⚠️ **本条不需要库，也不连网** —— 两个理由：
① 那道拦在**建 engine 之前**就抛 ⇒ 给它一个**不存在的 host** 它照样拦得住（用例 1 就是这么钉的）；
② 离线模式**本来就不碰库**（它只打印 SQL）。
⇒ 所以这四条用例在 CI 里也跑得动（CI 没有 postgres service）。
"""
import os
import pathlib
import subprocess
import sys

APP_DIR = pathlib.Path(__file__).resolve().parents[1]      # app/


def _run_alembic(*args):
    """跑一次 alembic。

    ⚠️ **用 `sys.executable -m alembic`，⛔ 不写 `venv/bin/alembic`** ——
       后者在 CI 里不存在（CI 把依赖装在跑测试的那个解释器里）。
    ⚠️ **cwd 必须是 `app/`** —— `alembic.ini` 在那儿。
    """
    env = dict(os.environ)
    # 指一个**不存在的库** —— 这道拦本该在连库之前生效，⛔ 不该走到连接那一步。
    env.update(POSTGRES_HOST="no-such-host.invalid", POSTGRES_PORT="1",
               POSTGRES_DB="no_such_db", POSTGRES_USER="nobody",
               POSTGRES_PASSWORD="nope")
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          cwd=str(APP_DIR), env=env,
                          capture_output=True, text=True, timeout=120)


def test_在线模式被拒绝_且抛的是我们那一句():
    """🔴 **本条是这道拦的守卫本身。**

    判据是**两件事**，⛔ 不只是"退出码非 0"：
    ① 非 0；② 输出里是我们**那句提示**（"已作废 / 拒绝在真库上执行"）。
    ⚠️ 只判非 0 是不够的 —— **连不上库**也会非 0，而那**不是**我们在防的事
       （本用例故意给了不存在的 host，所以这两种失败在退出码上**长得一样**）。
    """
    r = _run_alembic("upgrade", "head")
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, (
        "🔴 `alembic upgrade head` **成功了** —— 那道拦不在了！\n"
        "   它会 `drop_column('documents','requested_by')` ⇒ **静默拆掉多用户隔离**。\n"
        "   见 `app/alembic/env.py` 的 `_ONLINE_REFUSAL`。"
    )
    assert "已作废" in out and "拒绝在真库上执行" in out, (
        "🔴 非 0 了，但**不是被我们拦的** —— 是别的原因（多半是连不上库）。\n"
        "   两种失败在退出码上一样，所以判据必须落在**那句提示**上。\n"
        f"   实际输出尾巴：\n{(out or '')[-500:]}"
    )


def test_离线模式照旧可用():
    """⚠️ **拦过头会把既有判据一起弄坏** —— 离线模式只**打印 SQL**、不碰库，没有那个风险。

    它是 `app/alembic/CLAUDE.md` 里写着的既有判据（"离线生成 45 行 SQL，⛔ 不碰任何库"）。
    """
    r = _run_alembic("upgrade", "head", "--sql")
    assert r.returncode == 0, (
        "🔴 离线模式（`--sql`）被拦掉了 —— 那道拦**只该拦在线**。\n"
        "   它是既有判据，⛔ 别一起弄坏。\n"
        f"   stderr 尾巴：\n{(r.stderr or '')[-400:]}"
    )
    assert "COMMIT" in (r.stdout or ""), "离线模式该打印出 SQL（末尾有 `COMMIT;`）"


def test_history_照旧可用():
    """第三条既有判据 —— `alembic history` 读的是 `versions/`，**本来就不该受这道拦影响**。"""
    r = _run_alembic("history")
    assert r.returncode == 0, f"🔴 `alembic history` 跑不了了：\n{(r.stderr or '')[-400:]}"
    assert "f12a761ae626" in (r.stdout or ""), (
        "🔴 链条里看不到 `f12a761ae626` ⇒ 这条判据**在测别的东西**"
        "（本仓立场：枚举口径变了，这条就没在测东西）"
    )


def test_那条链里确实有那句危险的_drop_column():
    """**前面的用例在防什么，这一条把它钉出来** —— ⛔ 别让"为什么拦"只活在我的说明里。

    ⚠️ 若哪天这条链被**修对了**（的正解），**这一条会红** ——
       那正是它该有的行为：**修好之后回来把整道拦撤掉**，而不是留着装作还在防。
    """
    src = (APP_DIR / "alembic" / "versions" / "f12a761ae626_init.py").read_text(encoding="utf-8")
    assert "op.drop_column('documents', 'requested_by')" in src, (
        "🔴 那句 `drop_column` 不在了 —— 说明这条链**已经被修过**。\n"
        "   ⇒ **请回来把 `app/alembic/env.py` 那道 `raise` 撤掉**"
        "（换成 `run_migrations_online()`），并把本文件一起处理。\n"
        "   ⛔ 别让这道拦留在这儿，对一条已经安全的链假报风险。"
    )
