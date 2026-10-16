# `app/static/` —— 真前端（静态资源）

> 📇 🔴 **真前端代码在这里，⛔ 不在 `frontend/`** ——
> `frontend/` 里是**设计文档与索引**，代码是这一份。

## 📇 本目录索引
| 条目 | 是什么 |
|---|---|
| `web/` | **7 格 + 2 个格外的页**（`index` 总览 · `kb` 知识入库 · `rag` 内部RAG问答 · `agent` 外部检索与编排 · `ops` 系统观测 · `guide` 接入指南 · `reference` API参考 · **`stream` 流式问答＝`/rag` 的子页** · **`login` 登录＝所有页的前置**）—— 🔴 **2026-10-12 整批换过**，索引见 `web/CLAUDE.md`<br>📌 **现跑**（⛔ 别抄数）：`ls app/static/web/*.html \| wc -l` |
| `js/` | 前端 JS —— ⚠️ **⛔ 别在本行抄名单**（原先那句既漏了 `index`、又还写着 2026-10-15 已删的 `session` / `cred`）：**现跑** `ls app/static/js/*.js \| grep -v test`。分工 = **共用层**（🆕 `auth` · 🆕 `authbar` · `login` · `app-reveal`）+ **一页一 JS**（`rag` · `kb` · `agent` · `ops` · `index`）。用 `node --test` 跑，**零 npm 依赖** |
| `styles.css` | 🔴 **7 页引的唯一一张样式表** —— **起点**是从 `/3` 整份取的（`frontend/finally-定稿/形态稿/styles.css`），⚠️ **但早已不是"逐字一致"**（原先这里这么写着，已过期）：本仓在它上面**追加过**需求方点名的改动 —— 第 4 个色相 `--amber` 与 `.rail` 那一族的字色 · 页头下面那一行（`.auth-bar` / `.auth-strip`）· `.hi` 的 `flex-wrap`（**兜底**，窄窗口时整块换行而不是压扁导航）。<br>⇒ **改它就是改线上**，⛔ 别以为"改了形态稿那份才算数"（那份**不会**被页面引到）。📌 **现跑**：`diff app/static/styles.css frontend/finally-定稿/形态稿/styles.css \| wc -l`（⛔ 别抄数） |
| `specs/` | 前端的 spec（`static_frontend.md`） |
| `websocket_test.html` | WS 调试页（⛔ 不面向访客，不在那 7 页里） |

## 🔴 本层特有的规矩
- 🔴 **每个页面都要有「可点入口」** —— 本仓最高判据①：
  「**95% 的人不会去看代码**……**显示了，才知道你有做**」⇒ **藏进后端 = 等于没做**。
- 🔴 **必须`app.mount("/static", …)` 且路径锚在 `main.py` 旁边**（`os.path.dirname(__file__)`）
  ⇒ ⛔ **`main.py` 搬家会连带这里**。
- ⚠️ **前端的事优先看 `frontend/索引.md`**（需求方立的查找纪律：「**不用靠我来记，也不要用 grep**」）。
- ⚠️ **JS 纯逻辑用 `node --test`**（`app/static/js/*.test.js`），⛔ **不加 `package.json` / `npm install`**
  —— CI 里就是一条 glob + node 自带测试器。

## 📍 往上读
- `../CLAUDE.md`（`app/`）· `frontend/索引.md` · 仓库根 `CLAUDE.md`
