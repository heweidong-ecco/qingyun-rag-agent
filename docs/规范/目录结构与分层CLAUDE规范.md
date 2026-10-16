# 目录结构与分层 CLAUDE.md 规范

> 状态：**生效**（2026-10-09 起）。替代此前"扁平 `api/` + 一处集中索引"的组织方式。
> 决策记录：本仓的决策记录（⛔ 不随产品仓提供）。
> 执行过程：本仓的历史归档（⛔ 不随产品仓提供）。

## 一 · 适用范围

本仓（`fastapi-rag-agent`）的源码目录组织、模块归属、spec 位置、`CLAUDE.md` 分层方式。
不涉及：接口契约（见 `docs/契约/`）、部署（见 `docs/说明/部署.md`）。

## 二 · 容器与层级

| 项 | 规定 |
|---|---|
| 顶层容器 | **`app/`**。不是 `packages/`，不是 `src/` |
| 容器语义 | `app/` 是**部署单元的根**，同时是 **`sys.path` 的根**。它**不是 Python 包** |
| `__init__.py` | **不加**（靠命名空间包） |
| `src/` 层 | **不设**。理由：本仓不 `pip install`、不发布 wheel，`src/` layout 要解决的问题在此不存在 |
| Docker 构建上下文 | `./app`（`docker-compose.yml` 两个 service）。`WORKDIR /app` + `CMD uvicorn main:app` **保持不变** |

## 三 · 模块分组

`app/` 下按职责分为七组，另加四个非分组目录：

```
app/
├── access/   （8 项）
│   ├── specs/   （6 项）
├── agent/   （13 项）
│   ├── specs/   （9 项）
├── alembic/   （6 项）
│   ├── versions/   （6 项）
├── billing/   （7 项）
│   ├── specs/   （5 项）
├── core/   （11 项）
│   ├── specs/   （9 项）
├── eval/   （7 项）
├── rag/   （15 项）
│   ├── specs/   （11 项）
├── routing/   （10 项）
│   ├── specs/   （8 项）
├── specs/   （4 项）
│   ├── 归档/   （3 项）
├── static/   （6 项）
│   ├── js/   （13 项）
│   ├── specs/   （2 项）
│   ├── web/   （9 项）
├── tests/   （97 项）
├── tools/   （16 项）
│   ├── specs/   （14 项）
```

| 组 | 职责 |
|---|---|
| `core/` | 配置、数据库连接、缓存、指标、日志、LLM 工厂、异常 |
| `routing/` | HTTP / WebSocket 边界：路由表、依赖、Schema、SSE |
| `access/` | 鉴权、权限、会话、限流 |
| `billing/` | 计量、预算、熔断、成本面板 |
| `agent/` | Agent 编排、计划、审批、检查点、长期记忆 |
| `rag/` | 检索、解析、分块、向量、重排、引用 |
| `tools/` | 工具、MCP、执行器、缓存、健康 |
| `tests/` | 全部 pytest 用例（⚠️ **数现跑，⛔ 不写死** ⇒ `app/tests/CLAUDE.md` 开头那条命令） |
| `eval/` | 离线评估，不在服务路径上 |
| `specs/` | 不归属任何组的 spec（`main.md`）、模板、退役归档 |
| `static/` | 前端静态资源（真前端代码在此，不在 `frontend/`） |
| `alembic/` | 数据库迁移 |

**留在 `app/` 根的两个 `.py`，不得移动：**

| 文件 | 原因 |
|---|---|
| `main.py` | `uvicorn main:app` 与 Docker `CMD` 都绑定它 |
| `conftest.py` | pytest 根 conftest。它留在 `app/` 根，`app/` 才在 `sys.path` 上，`tests/` 的用例才能用裸导入 |

## 四 · 导入写法

**绝对导入，根为 `app/`**：

```python
from core.config import LLM_API_KEY
from billing.token_tracker import record_usage
```

| 写法 | 采用 | 原因 |
|---|---|---|
| `from core.config import X` | 是 | `app/` 已在 `sys.path` 上；不需 `__init__.py`；文件可被直接当脚本执行 |
| `from ..core.config import X` | 否 | 直接执行脚本时失败，且报错难读 |
| `from app.core.config import X` | 否 | 容器内 `/app` 就是 `app/` 的内容，没有 `app.` 这一层，线上会 `ImportError` |

## 五 · spec 的位置

**判据：spec 与它的模块同目录。**

| 模块 | spec |
|---|---|
| `app/core/config.py` | `app/core/specs/config.md` |
| `app/tools/mcp_server.py` | `app/tools/specs/mcp_server.md` |
| `app/main.py`（在 `app/` 根） | `app/specs/main.md` |
| `app/static/`（非 `.py` 子系统） | `app/static/specs/static_frontend.md` |

- 模板与写作规范：`app/specs/README.md`
- 对账：（**递归**扫 `app/**/specs/*.md`，自带防空跑）
- 新增模块必须同时建 spec（`pre-commit-gates.py` 第 ④ 道门**硬拦**）

## 六 · 分层 CLAUDE.md

每一层目录有一份 `CLAUDE.md`，它是**该层的索引表 + 主要内容**，不是"模块规则"。

**渐进式披露**：agent 进入某一层，先读该层的 `CLAUDE.md`；信息不够再往下层转。

| 加载时机 | 文件 |
|---|---|
| **每次会话都加载** | 仓库根 `CLAUDE.md` |
| **读写该目录文件时才加载**（懒加载） | 其余各层 `CLAUDE.md` |

**写法**（三类）：

| 类型 | 内容 |
|---|---|
| 有子目录（`app/`、`docs/`） | 列出下一层目录，每个一行：名称 + 职责 + 何时该往下 |
| 叶子目录但有内容 | 列出本目录文件，每个一行：名称 + 一句话 |
| 真空目录 | 三行小桩（声明"已检视、无特殊约定"），不要 0 字节 |

**不建 `CLAUDE.md` 的目录**：`venv/` `venv-ragas/`（依赖）· 被 `.gitignore` 的（`tmp/` `logs/` `archive/` `screenshots/` `self-prompt/` `showcase/设置与命令/`）· 自动生成的（本仓的工程配置（⛔ 不随产品仓提供））· 缓存目录。
> ⚠️ **`docs/可移植清单-开新项目时搬什么/scaffold-分层claudemd-gates-规矩门/新项目骨架/` 是【逐层都有 `CLAUDE.md`】的** ——
> 因为它是**要整份拷到新项目**的成品：**拷过去当天就得是绿的**（⛔ 不是「让新项目自己补」）。

> 🔴 **上面这份【就是】门的豁免名单，⛔ 别在别处再造一份。**
> 判据 ⇒ ****（**第 ⑧ 道提交门**，2026-10-10 加）。
> ⚠️ **两处不一致的表现是"门放行了规范不许放的东西"（或反之），而不报错** —— 本仓明文教训：
> **一个名单两个来源必然漂移**⇒ **改这条就同批改脚本里的 `NAME_EXEMPT` / `PATH_EXEMPT`**。

**边界**：`CLAUDE.md` 答"这里有什么、在哪"；
**逐模块的"做到哪"⇒ `app/<组>/specs/<模块>.md`** · **前端各刀 ⇒ `frontend/索引.md`**
**阶段级 / 跨模块的经过 ⇒ `docs/history` 那类的 ` · **`ROADMAP` 答"当前状态与顺序"**。

> 🔴 **2026-10-10 改**：原句是「`ROADMAP` 答"做到哪了"· **不要把进度写进 `CLAUDE.md`**」——
> 前半句随需求方裁「甲」**改了**（进度**按归属分发**，`ROADMAP` 只留当前那一块）。
> ⚠️ **后半句仍有效，但要读准**：⛔ **逐模块的过程记录**不许写进 `CLAUDE.md`；
> **允许**的是**本组【整组级】的进度一两行**（🔴 **必须指向 `specs/`**，⛔ 不展开）——
> 见 `app/tools/CLAUDE.md` 的「🟡 本组做到哪」那个样子。

## 七 · 数据文件跟随模块

凡用 `dirname(__file__)` 或 `with_name()` 计算路径的数据文件，**与使用它的模块同目录**，这样移动模块时**不需要改代码**。

| 数据文件 | 跟随的模块 |
|---|---|
| `cleanup_rules.json` | `app/rag/document_preprocessor.py` |
| `agent_history.db` | `app/agent/agent_checkpointer.py` |
| `mcp_server.py` | 被 `app/agent/agent_graph_advanced.py` 以 `with_name()` 定位，两者必须同组 |

例外（不跟随，需改代码）：`app/core/config.py` 读仓库根 `.env`、`app/core/logger_config.py` 写 `app/logs/`、`app/tools/browser_tools.py` 写仓库根 `screenshots/`。

## 八 · 判据

```bash
ls app/main.py app/conftest.py                 # 两个入口留在 app/ 根
ls app/*/specs/*.md | wc -l                    # spec 分散在各组
# 模块 ↔ spec 对账（56 有 / 0 缺）
# 断链门
# 🆕 分层 CLAUDE.md 门（每个目录层都得有它）
--self-test   # 🆕 它的自测（5 条）
./venv/bin/python -m pytest app/ -m "not integration and not needs_db" -q
grep -rn "api/" --include='*.py' app | grep -v "/api/v1"   # 应只剩 URL 前缀
```

## 九 · 变更历史

| 日期 | 变更 |
|---|---|
| 2026-10-09 | 建立本规范。`api/` 拆七组 + 容器改名 `app/`（`#123`）· spec 拆进模块 · 分层 `CLAUDE.md` 全覆盖 |
| 2026-10-10 | §六 **补上第 ⑧ 道提交门**—— 在此之前这套机制**只有文字、没有门**；§六 的「边界」那句随需求方裁「甲」修正 |

详细过程与当时的备选方案见 本仓的历史归档（⛔ 不随产品仓提供）。
