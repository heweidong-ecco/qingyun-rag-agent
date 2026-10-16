# `app/static/specs/` —— 前端的 spec

> 📇 前端是**唯一没有 `.py` 模块**的子系统 ⇒ 它的 spec 用**固定的名字**，不跟模块名走。

## 📇 本目录索引

| spec | 对应 |
|---|---|
| `static_frontend.md` | `app/static/`（`web/*.html` · `js/*.js`） |

## 🔴 本层特有的规矩
- ⚠️ **前端的"模块对账"特殊**： 里有一张 `NON_PY_MODULES` 表
  把 `static_frontend` **显式登记** —— ⛔ 不然它会被报成「spec 有、代码没了」（假警报）
- ⭐ **前端的事优先看 `frontend/索引.md`**（作者 2026-10-09 立的：
  「**不用靠我来记，也不要用 grep 去找**」）

## 📍 往上读
- `../CLAUDE.md`（`app/static/`）· `frontend/索引.md` · 仓库根 `CLAUDE.md`
