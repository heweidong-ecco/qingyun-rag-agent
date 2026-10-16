#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给页面里引的静态资源**盖上内容哈希**：`/static/js/kb.js` ⇒ `/static/js/kb.js?v=9f3a1c2e07`。

## 它在治什么

2026-10-15 发布后，作者在线上**撞到过**：「登录之后按钮没变成"已登录"、额度那一行不见了」。
根因不是发布错了 —— 是**浏览器缓存了旧的 `authbar.js`、而 HTML 是新的**：
旧 JS 找的是旧挂点（`#auth-in`/`#auth-out`），新版已改名（`#auth-btn`/`#auth-strip`）
⇒ `setHidden()` **对不存在的元素静默跳过** ⇒ **按钮不换字、那一行不露，且不报错**。

⚠️ **为什么本仓躲不掉**：`/static/` 原先**只有 ETag/Last-Modified、没有 `Cache-Control`**
⇒ 浏览器走**启发式缓存** ⇒ **每次发布都可能有人拿到"新 HTML + 旧 JS"**。

## 它怎么治

**把内容哈希写进 URL** ⇒ 内容一变 URL 就变 ⇒ 浏览器**可以放心长期缓存**（配套见 `app/main.py`
那个 `_ImmutableStaticFiles`：`.js`/`.css` 走 `max-age=31536000, immutable`）。
⚠️ **HTML 自己不走长缓存**（它的 URL 不带哈希）—— 它每次协商一次，命中就回 304。

## ⛔ 为什么必须是脚本、而不是"我下次记得改"

手写版本号（`?v=2` → `?v=3`）**必然会被忘**，而忘了之后**表现和现在一模一样**（半新半旧、不报错）
—— 只是更隐蔽，因为你会以为"加了版本号就没事了"。
⇒ 版本号**从文件内容算**，忘不了；再加一条门盯着（`app/tests/test_web_pages.py` 里那条）。

## 用法

```bash
venv/bin/# 就地改（把 8 个页面里的引用刷成当前哈希）
venv/bin/--check  # 只查不改 —— 门里用的就是这条
```

🔴 **改了 `app/static/` 下任何一个 `.js`/`.css` 之后，都要重跑一次它**（门会提醒你）。
"""
import hashlib
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
WEB = REPO / "app" / "static" / "web"
STATIC = REPO / "app" / "static"

#: 只盖这两类 —— `.html` **有意除外**（它自己就是"哪一版"的载体，URL 带哈希反而绕回去）
STAMP_SUFFIXES = (".js", ".css")

#: `<script src="/static/…">` / `<link href="/static/…">`，**允许已经带着 `?v=`**（重跑幂等）。
REF = re.compile(
    r'(?P<attr>\b(?:src|href)=")(?P<url>/static/[^"?]+?\.(?P<ext>js|css))(?:\?v=[0-9a-f]+)?(?P<close>")'
)


def content_hash(rel_url: str):
    """`/static/js/kb.js` ⇒ 它当前内容的短哈希（10 位十六进制）。

    ⚠️ **文件不在 ⇒ 回 `None`**（⛔ 不抛）：页面引一个**已经被删**的脚本，是**真事**
       —— 本脚本第一次跑就撞上了：`guide.html` 还引着 2026-10-15 删掉的
       `session.js` / `cred.js` ⇒ **那一页每次加载都打两个 404，而全仓没有任何门会红**。
       ⇒ 这种事**必须报出来**，⛔ 不是让脚本崩在半路（崩了 = 后面的页面一个都不扫）。
    """
    f = STATIC / rel_url[len("/static/"):]
    if not f.is_file():
        return None
    return hashlib.md5(f.read_bytes()).hexdigest()[:10]


def stamp(text: str):
    """把一页里的引用全刷成当前哈希。

    返回 `(新文本, 改了几处, 引不到的 [url, …])` —— ⚠️ **引不到的原地不动**（⛔ 不硬改）。
    """
    n, missing = 0, []

    def sub(m):
        nonlocal n
        url = m.group("url")
        h = content_hash(url)
        if h is None:
            if url not in missing:
                missing.append(url)
            return m.group(0)                     # ⛔ 原地不动
        n += 1
        return f'{m.group("attr")}{url}?v={h}{m.group("close")}'

    return REF.sub(sub, text), n, missing


def check(text: str):
    """只查：返回 `[(url, 页面里的版本号, 当前真实哈希), …]`。空 = 全对。"""
    bad = []
    for m in REF.finditer(text):
        url = m.group("url")
        want = content_hash(url)
        got = re.search(r"\?v=([0-9a-f]+)", m.group(0))
        if want is None:
            bad.append((url, got.group(1) if got else "(没盖)", "🔴 这个文件不存在（引了 404）"))
        elif got is None:
            bad.append((url, "(没盖版本号)", want))
        elif got.group(1) != want:
            bad.append((url, got.group(1), want))
    return bad


def main() -> int:
    do_check = "--check" in sys.argv
    pages = sorted(WEB.glob("*.html"))
    if not pages:
        print(f"⛔ {WEB} 下一个 html 都没有 —— 扫描面错了？", file=sys.stderr)
        return 2

    changed = bad_total = dead_total = 0
    for p in pages:
        src = p.read_text(encoding="utf-8")
        if do_check:
            bad = check(src)
            if bad:
                bad_total += len(bad)
                print(f"🔴 {p.relative_to(REPO)}")
                for url, got, want in bad:
                    print(f"     {url}  页面里是 {got} · 现在应该是 {want}")
            continue
        new, n, missing = stamp(src)
        if missing:
            # 🔴 **引了不存在的文件** —— 这比"版本号过期"更严重：那一页每次加载都打 404
            dead_total += len(missing)
            print(f"🔴 {p.relative_to(REPO)} 引了不存在的文件：{missing}"
                  f"  ⇒ 删掉那几行引用，或者把文件找回来")
        if new != src:
            p.write_text(new, encoding="utf-8")
            changed += 1
        print(f"  {'改了' if new != src else '没变'}  {p.relative_to(REPO)}  （{n} 处引用）")

    if do_check:
        if bad_total:
            print(f"\n🔴 {bad_total} 处对不上 —— 版本号类的跑 "
                  f" 刷一遍；"
                  f"「文件不存在」类的**要人去删引用或找回文件**")
            return 1
        print(f"✅ {len(pages)} 个页面里的静态资源版本号**全是当前哈希**，且**没有引不到的文件**")
        return 0

    print(f"\n✅ 扫了 {len(pages)} 个页面 · 动过 {changed} 个" + (f" · 🔴 {dead_total} 处引了不存在的文件" if dead_total else ""))
    return 1 if dead_total else 0


if __name__ == "__main__":
    sys.exit(main())
