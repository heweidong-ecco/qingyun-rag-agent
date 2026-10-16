# `app/static/js/` —— 前端纯逻辑

> 📇 **一页一 JS**，且纯逻辑**都有 `node --test` 单测**（零 npm 依赖）。

## 📇 本目录索引
| 文件 | 哪一页 | 单测 |
|---|---|---|
| `app-reveal.js` | 🔴 **共用（新 7 页）**：滚动揭示 —— 从 `/3` 的 `app.js` **只搬两块**（`initReveal` + `syncHeader`），阈值逐字照抄 | —（⛔ 无单测：它是纯 DOM 副作用，逐字照搬的第三方片段） |
| 🆕 **`auth.js`** | 🔴 **共用（登录层）**：**登录态（JWT）的唯一一份** —— 登入 / 登出 / 拿令牌 / 刷新 · ⭐ **`apiFetch`**（页面取数的**唯一入口**：贴认证头 + 401 换一把令牌重试一次 + **并发 401 只刷一次**）（§七·1） | 🆕 `auth.test.js`（**25 条**） |
| 🆕 **`authbar.js`** | 🔴 **共用（登录层）**：**页头那块登录态控件的唯一一份** —— 「已登录」（+ 今日额度 + 退出）⇄「去登录」（带 `next=` 回本页）。⚠️ 它替的是 `cred.js` | 🆕 `authbar.test.js`（**13 条**） |
| `rag.js` | **内部RAG知识库检索及问答**（`/rag`）· 接口切换器 + 四档 mode + 引用/资料两种格子 | `rag.test.js` |
| `kb.js` | **知识入库**（`/kb`）· 三种入库 + 「库里有什么」· ⚠️ `DOC_TYPES` 是**跨语言**钉住的那份 | `kb.test.js` |
| 🆕 `ops.js` | **系统观测**（`/ops`）· 🔴 **判决三态**（取不到 ⇒ 掉出 ok）+ 七处假正常的处置 + Prometheus 文本只摘不解析 · | 🆕 `ops.test.js`（**24 条**） |
| 🆕 `agent.js` | **外部检索与工具Agent编排**（`/agent`）· 🔴 **链列表**（三条链的 `has` 表）+ SSE 分帧 + 人工接管（批/拒/代填）· | 🆕 `agent.test.js`（**23 条**） |
| 🆕 `index.js` | **总览 / 首页**（`/`）· 🔴 **真数当场算**（接口条数 ← `/openapi.json`；缺值画「—」⛔ 不画 0）+ 真返回 → 格子的映射 · | 🆕 `index.test.js`（**21 条**） |
| 🆕 **`login.js`** | **登录**（`/login`）· 🔴 **`?next=` 那个判定**（`safeNext` —— **防开放重定向**：只收站内路径，`//host` / `https://` / `javascript:` / `/\host` 一律回 `/`）· §七·1 | 🆕 `login.test.js`（**13 条**） |

> ⚠️ **共用层那四份**（`auth` · `login` · `authbar` · `app-reveal`）**每页都引**；**一页一 JS** 那几份只有本页引。
> 🔴 **`login.js` 也在共用层** —— 它那对 `safeNext` / `loginHref`（`?next=` 的**解**与**编**）
>    **必须住同一个文件**（分居两处 = 赌"改了这头记得改那头"）。⚠️ 所以 6 个内容页也引它。
> 📌 **现跑**：`ls app/static/js/*.js | grep -v test | wc -l`（⛔ 别抄数）。

> ## 🔴 2026-10-12：**旧那 12 份 js 全部删掉**（前端重做 ⑥）
>
> `sse.js` · `cost.js` · `lab.js` · `tools.js` · `system.js` · `ops.js` · `trace.js`
> `approvals.js` · `kb.js` · `panel.js` · `pagenav.js` · `topnav.js`（+ 各自的 `.test.js`）
> —— 它们服务的**旧页面已经删了**，索引在 `app/static/web/CLAUDE.md`。
>
> 🔴 **两条随之作废的约定**（⛔ 别照着旧文找）：
> - 「`panel.js` 必须在各页 js 之前」—— `panel.js` 没了；新的加载顺序只有一条：
>   **`session.js` → `cred.js` → `app-reveal.js`**。
> - 「`RagKb.DOC_TYPES` 必须等于后端 `CHUNK_CONFIGS`」那条**跨语言守卫**——
>   它守的规矩仍然有效（页面给一个后端不认的档位 ⇒ 传上去当场 **400**，而**没有任何门会红**），
>   ✅ **落点回来了**（已结清）：**`app/tests/test_doc_types_crosslang.py`** 钉**新 `kb.js` 的 `DOC_TYPES`**
>   （用 `node -e "require('./kb.js').DOC_TYPES"` 读那份**导出**，⛔ 不是拿正则抠字面量）。


## 🔴 本层特有的规矩
- 🔴🔴 **每个文件整体包在 IIFE 里，对外⛔ 只许留【一个】全局键**（`RagXxx`）。
  ⚠️ **经典脚本（`<script src>`）的顶层 `function` / `const` 全是全局的** ——
  而 8 个页面**各自也有顶层绑定**（每页都有 `const $ = …`、`const key = …`、`function el(…)`）。
  2026-10-10 实测：`session.js` 顶层写 `function key()` ⇒ 与页面那句顶层 `const key` **撞上**
  ⇒ `SyntaxError: Identifier 'key' has already been declared`
  ⇒ **整段内联脚本一行都不执行，8 个页面全是死的**，而 982 条 pytest + 283 条 node **全绿**。
  ⇒ 现在两份共用文件各包一层 IIFE；**守卫**：两个 `*.test.js` 末条钉「只许漏一个全局」（反证过）。
  📄 复盘
- 🔴 **页面里那段【内联】JS 也有门了**（2026-10-10 新立）——
  `app/tests/test_web_pages.py` 会把每页内联的 `<script>` 抽出来过 `node --check`。
  ⚠️ 它只管「**能不能解析**」；逻辑对不对仍归本目录的 `*.test.js` 与各页用例。
  ❗**在这之前，那段代码没有任何判据** —— 写错了没人知道（同上面那份复盘）。
- 🔴 **每个页面都要有「可点入口」**（最高判据①）—— **⛔ 不许把能力只做在后端**
- 🔴 **⛔ 不加 `package.json` / `npm install`** —— CI 就一条 glob：
  `node --test app/static/js/*.test.js`
  ⚠️ 那条 glob **自带防空跑**（匹配不到文件 ⇒ 红）—— ⛔ 别把守卫删了
- 🔴 **界面里那几条"边界文案"只许有一份**（结构型守卫：数的是**文件**）
- ⚠️ **请求字面量必须以 `/api/v1` 开头**（页面守卫会扫）

## 📍 往上读
- `../CLAUDE.md`（`app/static/`）· `frontend/索引.md` · 仓库根 `CLAUDE.md`
