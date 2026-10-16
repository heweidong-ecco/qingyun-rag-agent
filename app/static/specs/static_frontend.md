# `app/static/` —— 前端（**本仓唯一没有 `.py` 模块的子系统**）

| 项 | 内容 |
|---|---|
| **状态** | ✅ **已接线** —— **7 格 + 2 个格外的页**（`stream` 是 `/rag` 的子页 · `login` 是所有页的前置）。🔴 **走【正规登录】**（§七·1）—— ⛔ 不再有"访客凭据"那套 |
| 🔴 **找前端的东西** | ⭐ **`frontend/索引.md`** —— **唯一查找入口**（作者 立的：「**不要用 grep 去找，也不准**」） |
| **对外提供** | **9 条 302**（`/` `/kb` `/rag` `/stream` `/agent` `/ops` `/guide` `/reference` `/login`）⇒ `/static/web/*.html`，全部 `include_in_schema=False`。页面本体由已挂的 `/static` 托管（**零构建、零新服务、零 CORS**） |
| **谁在用** | 人（浏览器）。⚠️ **后端不 import 它、没有任何 `.py` 依赖它** —— 这是它长期"没人管"的原因 |
| **目录索引** | `app/static/CLAUDE.md` · `app/static/web/CLAUDE.md` · `app/static/js/CLAUDE.md`（**那三份是跟上了的**；本文件只写**从代码里读不出来的那部分**） |
| **旧骨架** | 🗂 **整批换过**（：旧 10 页 + 旧那张样式表 + 12 份 js **全删**）。⚠️ **重写前那份 487 行的 spec 已剪进** （⛔ **不是删掉** —— 本仓规矩：原始记录不改写） |

---

## ✅ 做了什么

- **9 页 / 9 份非测试 js**，纯逻辑**全有 `node --test` 单测**（零 npm 依赖）。
  判据（现跑）：`node --test app/static/js/*.test.js` ⇒ 末行 `pass 161 / fail 0`。
- **登录层（· §七·1）** —— 三份共用文件，**各自只有一份实现**：
  · `js/auth.js` = **登录态（JWT）的唯一一份** + ⭐ **`apiFetch`**（页面取数的**唯一入口**：
    贴认证头 · 401 换一把令牌重试一次 · **并发 401 只刷一次**）
  · `js/login.js` = **`?next=` 那个判定**（`safeNext` —— 防开放重定向）；**登出/入口链接的"编"也在这一份**
    （编与解**必须住同一个文件** —— 分居两处就是赌"改了这头记得改那头"）
  · `js/authbar.js` = **页头那块登录态控件的唯一一份**（「已登录」⇄「去登录」）
- **一页一 JS**（`index` · `kb` · `rag` · `agent` · `ops`）+ 共用层 `js/app-reveal.js`（滚动揭示）。
  ⚠️ **`stream.html` 的真调用写在【内联脚本】里** —— 它是 `/rag` 的子页，没有自己的 js 文件。
- **两页【有意】没有自己的 js**，⛔ **别当欠账去补**：
  · `guide.html` —— **纯文档、零 token**，它**一个请求都不发**
  · `reference.html` —— 🔴 **由探针机械生成**，⛔ **不手改**（改它 = 改那份探针）
- **样式表只有一张**：`app/static/styles.css`（整份取自 `/3`，与形态稿逐字一致）。
- **旧的两套凭据机制已删干净**：`js/session.js` / `js/cred.js`（"首访静默领一把 API Key"）
  随「不再有访客」一起下线。页面那 8 份页头控件由 `authbar.js` 接替。

## 🟡 做到哪 / 缺什么

| # | 项 | 状态 |
|---|---|---|
| 1 | **前端那一批增量**（形态 / 内容 / 首页 / 布局） | ⏳ **主线，等作者给输入** ⇒  第一屏那块「下次从这里接」 |
| 2 | 🔴 **`accurate` / `full` 两个 mode 点了会 500** | ⬜ **按「做不到就声明」处置**（⛔ 不留能点的死按钮）—— 主镜像不装 reranker（重裁）。<br>⚠️ **现状**：`rag.html` 那两颗按钮**已经是 `disabled`**，但 `title` 上写的是「本服务镜像不装」⇒ **措辞过期**（新主线下没有"本服务镜像"这回事） |
| 3 | 🔴 **`guide.html` 还在引两个【已删】的文件** | ⚠️ **查出**：`app/static/web/guide.html:267-268` 仍写着 `<script src="/static/js/session.js">` 与 `cred.js`，而那两份**当天已经删了**⇒ 两条 **404**。<br>⚠️ **不影响功能**（那页不取数、`app-reveal.js` 照常加载），但**是坏引用**。<br>📌 判据：`grep -n 'session.js\|cred.js' app/static/web/guide.html` ⇒ **2 条**，而 `ls app/static/js/` 里**没有这两个文件** |
| 5 | **DOM 那层没有自动判据** | 滚动 / 按钮态 / 版式仍靠手工（本仓页面守卫判的是**源码文本**，⛔ 不是像素）。⚠️ 本仓因此在 栽过一次：**11 条页面守卫全绿，而 `<textarea>` 已经溢出到格子外面** |

## ⚠️ 看代码会误判的地方 ⭐

> **只列【读代码推不出来】的**。前两节那些从代码里能看出来的，不在这里重复。

| 看代码会以为 | 实际 |
|---|---|
| 🔴🔴 **「经典脚本里写个 `function key` 有什么关系」** | ⛔ **有关系，而且致命**。`<script src>` 是**经典脚本** ⇒ 顶层 `function` / `const` **全是全局的** ⇒ 会与**页面自己的顶层绑定**撞上（每页都有 `const $ = …`）⇒ `SyntaxError` ⇒ **那一整段内联脚本一行都不执行**。<br>🔴 **真的栽过**：8 个页面**全是死的**，而 982 条 pytest + 283 条 node **全绿**（那些只读文本、只测 `js/*.js` 自己）。⇒ 现在**每个 js 整体包一层 IIFE，对外只留一个全局键**（`RagXxx`）。<br>📄 复盘 ⇒ |
| 🔴 **「页面里那段【内联】JS 也没有门」** | ⛔ **有**：`app/tests/test_web_pages.py` 把每页内联的 `<script>` 抽出来过 **`node --check`**。⚠️ **它只管「能不能解析」** —— 逻辑对不对仍归各 `*.test.js` 与各页用例。 |
| 🔴 **「`app/tests/test_web_pages.py` 全绿 ⇒ 每个页面都验过了」** | ⛔ **它有几处【空过】**，而且是**两族**：<br>① **页面走 helper 拼路径**（本仓好几页都是：路径由本页 js 里一个 `buildRequest` 一类的函数出）⇒ 页面里没有带 `/api/v1` 的 URL 字面量 ⇒ **那几条前缀不经过它** ⇒ 尺子在**本页 js 的 `*.test.js`** 里。<br>② **页面一个 `fetch` 都没有**（`guide` / `reference`）⇒ 没有字面量可查。<br>⚠️ 它自带一条**防空跑**用例，但那条只保证「**它扫过的集合非空**」，⛔ **不保证每一页都被真的检查到**。 |
| 🔴 **「页面里可以写相对路径 `/agent/…`」** | ⛔ **不行** —— 后端路由前缀是 `/api/v1`，页面由 `/static` 托管**同源** ⇒ 相对路径**不会被补前缀**，只会打到一个**不存在的路径**上（**404，而页面上通常看不出是路径错**）。<br>⇒ 门 = `test_web_pages.py::test_page_url_literals_carry_the_api_prefix`。⚠️ **例外**：`/health` `/ready` `/metrics` **就是根路径**，写成字面量反而会被那条门拦下 —— 所以系统观测那类页面**一个字面量都不写**。 |
| 🔴 **「用 `fetch` 还是 `apiFetch` 随便，都是发请求」** | ⛔ **页面取数一律走 `RagAuth.apiFetch`**（§七·1）—— 认证头、**令牌过期换一把重试**、**并发 401 只刷一次**，三件事都收在那一个入口里。裸用 `fetch` ⇒ 那三件**一件都没有**，而**页面上不报错**（只是 401 之后一直 401）。<br>⚠️ **两条门的正则也认 `apiFetch(`** —— 只认 `fetch(` 会让它们**静默空掉**（当场栽过一次）。 |
| 🔴 **「这几份 js 该写成 ES module（`import` / `export`）」** | ⛔ **不能**。本仓**没有 `package.json`** ⇒ Node 把 `.js` 当 **CJS** ⇒ 改 `import` 会让 `node --test` 报 `Cannot use import statement outside a module`，**而浏览器里照样能跑** ⇒ **改坏了本地不报错**。<br>⇒ 每个 js 的两侧写法是：**顶层函数 + 末尾条件挂 `window.RagXxx` / `module.exports`** —— ⚠️ **那两行不是重复**，删掉任一侧都会让**另一侧静默失效**（浏览器 `ReferenceError` / `node --test` 照样全绿）。 |
| ⚠️ **「`hidden` 属性总能藏住元素」** | ⛔ **作者样式的 `display` 压过 UA 的 `[hidden]{display:none}`** ⇒ 元素会**照常显示**（本仓真栽过：登录页与主界面**同时显示**）。<br>⇒ `styles.css:189` 那条 `[hidden] { display: none !important; }` 是**全页**的兜底，⛔ 别以为它只是给某一个元素打的补丁。 |
| 🔴 **「`reference.html` 是手写的，改一行没关系」** | ⛔ **它是探针机械生成的** ⇒ 手改 = **下一次重跑探针就没了**，而且没人会发现。要改就改**那份探针**（`qingyun-rag-agent-TODO待办/探针-生成API参考-上线页-20261013.py`）。 |
| 🔴 **「`guide.html` 没有 `js/` ⇒ 是漏做了」** | ⛔ **有意为之**（作者 定）：它是**纯文档页、零 token**，**不发任何请求** ⇒ 给它补一个 `guide.js` 等于**把"真跑"加回来**。 |
| 🔴 **「页脚那行可以写死『N 条』更清楚」** | ⛔ **不许** —— 条数由**首页那一块当场数**（`#n-bounds`），各页页脚**只指路**（`见首页「这套服务的边界」`）。写死会漂（已经因为写死「5 条」改过一次）。<br>⚠️ **边界不在首页首屏** —— 它排在**能力地图之后**（`test_index_page.py` 有一条专门钉这个次序）。 |
| ⚠️ **「页面里那个数取不到就显示 0」** | ⛔ **不许** —— 硬约束①：**页面里⛔ 不许有一个"编的"数**，取不到一律画 **`—`** + 空态说明。⚠️ 判空要写「**它得是个有限的数**」，⛔ 不是 `!== undefined` —— 后者挡不住 `null`，而 `null` 拼进字符串**不报错、只印出 `NaN`**。 |
| ⚠️ **「页面渲染文案里写 `**强调**` 没问题」** | ⛔ **它会把两个星号原样印在页面上** —— 本仓页面一律 `textContent`，**markdown 记号不会被渲染**。⇒ 有一条全站守卫 `test_web_pages.py::test_page_does_not_print_markdown_marks`。 |
| 🔴 **「全站导航那 7 格是装饰，少一格没关系」** | ⛔ **有一条门**：`test_web_pages.py::test_every_page_nav_links_to_all_seven`。缺一格的后果是「**从那条路进不去那一页，而那一页看起来完全正常**」。 |
| ⚠️ **「`/login` 那个 `?next=` 直接拿来跳就行」** | ⛔ **那是一处开放重定向面** —— `js/login.js` 的 `safeNext` 只收**站内路径**，`//host` / `https://` / `javascript:` / `/\host` **一律回 `/`**。⚠️ 改它之前先看 `login.test.js`。 |

## 判据（可打印）

```bash
# ① 纯逻辑（自动的，CI 里也有）
node --test app/static/js/*.test.js # ⇒ 末行 pass 161 / fail 0（现跑；⚠️ 数量别抄）

# ② 页面守卫（Python · 不连库）
venv/bin/python -m pytest app/tests/test_page_routes.py -q   # 9 条路由：302 / 目标在盘上 / 不在 openapi / login 保留 ?next=
venv/bin/python -m pytest app/tests/test_web_pages.py -q     # 内联脚本 node --check · URL 字面量带前缀 · 7 格导航 · 不印 markdown 记号
venv/bin/python -m pytest app/tests/test_index_page.py -q    # 首页：无外网 CDN · 能力地图列全 · 边界块在后面 · 条数不写死
venv/bin/python -m pytest app/tests/test_guide_page.py -q    # 接入指南：纯文档（它一个请求都不发）
venv/bin/python -m pytest app/tests/test_doc_types_crosslang.py -q   # 🔴 跨语言：kb.js 的 DOC_TYPES ≡ 后端 CHUNK_CONFIGS 的键
venv/bin/python -m pytest app/tests/test_frontend_contract.py -q     # 对端（后端那半，页面消费的契约）

# ③ ⛔ 写不成命令的（手工 —— 本仓页面守卫判的是源码文本，⛔ 不是像素）
# · 版式 / 溢出 / 滚动 / 按钮态 —— 正是这类"没有尺子"的地方栽的（textarea 溢出而 11 条守卫全绿）
#   · SSE 真实流式：Network 里 type = text/event-stream，正文增长时连接未关
#   · 「点了有没有真调」：devtools 里看是 200 还是 401/404
```

## 关联

- 🗂 **旧骨架（重写前那份 487 行）**
- 🔴 ** 主线替换**
  （§七·1 = **正规登录 + `apiFetch` 收口** · 它是**本轮删除的根据**）
   
  重排依赖不装（理由见 `docs/原理/` 的架构说明）
- 📄 复盘 ⇒ （**IIFE 那条的由来**）
- 🔴 **设计文档不在本目录**：形态稿 / 每一刀的过程 ⇒ **`frontend/索引.md`** 与 `frontend/finally-定稿/`
  （⛔ `frontend/` 里**只有设计文档**，**真前端代码在 `app/static/`**）
