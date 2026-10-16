# `app/` —— 应用本体（容器）

> 📇 **这一层是【索引表 + 主要内容】** —— agent 进到 `app/`，先读这份；
> 信息不够，再按下面那张表**往下一层转**。

## 📇 本目录索引

| 子目录 | 一句话 | 它的 `CLAUDE.md` |
|---|---|---|
| `core/` | **地基**：配置 · 连接 · 缓存 · 指标 · 日志 · LLM 工厂 · 异常（8 个） | `core/CLAUDE.md` |
| `routing/` | **HTTP / WS 边界**：3 张路由表 + 依赖 + Schema + SSE（7 个） | `routing/CLAUDE.md` |
| `access/` | 鉴权 · 权限 · 会话 · 限流（5 个） | `access/CLAUDE.md` |
| `billing/` | 计量 · 预算 · 熔断 · 成本面板（4 个） | `billing/CLAUDE.md` |
| `agent/` | Agent 编排 · 计划 · 审批 · 检查点 · 长期记忆（9 个） | `agent/CLAUDE.md` |
| `rag/` | 检索 · 解析 · 分块 · 向量 · 重排 · 引用（10 个） | `rag/CLAUDE.md` |
| `tools/` | 工具 · MCP · 执行器 · 缓存 · 健康（13 个） | `tools/CLAUDE.md` |
| `tests/` | **pytest 用例**（⚠️ **数一律现跑，⛔ 不写死** —— 本格原先写着「94 个」） | `tests/CLAUDE.md` |
| `eval/` | 离线评估（RAGAS 那套，⛔ 不在服务路径上） | `eval/CLAUDE.md` |
| `specs/` | **不归属任何组的 spec**（`main.md`）+ 模板 + 退役归档 | `specs/CLAUDE.md` |
| `static/` | **真前端**（22 个：`web/*.html` · `js/*.js`） | `static/CLAUDE.md` |

**留在 `app/` 根的 2 个 `.py`**（⛔ 别挪）：
- **`main.py`** —— 服务入口。`uvicorn main:app` 与 Docker `CMD` **都绑它** · spec ⇒ `specs/main.md`
- **`conftest.py`** —— pytest 根 conftest。⭐ **它一留，`app/` 就在 `sys.path` 上**
  `tests/` 里的用例才能用**裸导入**（`from main import app`）

其余非 `.py`：`Dockerfile` · `executor.Dockerfile` · `.dockerignore` · `requirements.txt`
`pytest.ini` · `schema.sql`。

## 🔴 本层特有的规矩

1. 🔴 **做完一个模块 ⇒ 更新它的 spec**（判据：你动了 `app/` 下的模块并做完某件事）
   - **新增模块** ⇒ **必须同时建 spec** —— ⛔ **`pre-commit-gates.py` 会【硬拦】**
   - **改已有模块** ⇒ 更新它（**hook 只提醒，不拦**）
   - ⭐ **最该写的是「⚠️ 看代码会误判的地方」那一节** —— 前两节读代码也能推出来，**只有这节推不出来**
2. 🔴 **spec 与它的模块【同目录】**：`app/core/config.py` ⇒ `app/core/specs/config.md`；
   `app/` 根的模块（`main.py`）⇒ `app/specs/main.md`
3. ⚠️ **`app/` 是 `sys.path` 的根** ⇒ 导入写 `from core.config import X`（⛔ **不是** `from app.core...`）
4. ⚠️ **别把数据文件与它的模块拆开**：凡用 `dirname(__file__)` 算路径的
   （`cleanup_rules.json` · `agent_history.db` · `mcp_server.py`）**跟着模块走**，否则**静默读不到**

## 📍 往上读

- 仓库根 `CLAUDE.md` —— **全局约定**（最高判据 · 推送节奏 · 判据纪律 · skills 表）
