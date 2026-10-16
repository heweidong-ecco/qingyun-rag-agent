# 贡献指南

> ## 先读这一句
>
> 这是一个**单人项目** —— 所以本文件主要讲**怎么把它跑起来 / 怎么验它**，
> 而不是"多人协作的 PR 流程"。

---

## 一 · 五分钟把环境跑起来

```bash
# ① 配置（⚠️ .env 必须在【仓根】，⛔ 不是 app/ 下）
cp .env.example .env
#    然后填 4 个必填键：DASHSCOPE_API_KEY · POSTGRES_PASSWORD · JWT_SECRET_KEY · LOGIN_PASSWORD
#    ⇒ 逐键说明见 docs/契约/环境变量.md

# ② 起服务（api / postgres / redis / executor …）
docker compose up -d

# ③ 验证（判据是 HTTP 码，⛔ 不是"我觉得起来了"）
curl -s -o /dev/null -w '/health %{http_code}\n' http://127.0.0.1:8000/health   # 200
curl -s -o /dev/null -w '/ready  %{http_code}\n' http://127.0.0.1:8000/ready    # 200（⚠️ 启动 10 秒内可能 503，正常）
```

> ⛔ **`docker compose down` 不要加 `-v`** —— 那会**删卷 = 数据全没了**。

**起不来** ⇒ 先查 [`docs/说明/运维.md`](docs/说明/运维.md) 的**常见故障表**。

---

## 二 · 本地开发（热重载，不用重建镜像）

```bash
bash dev.sh          # 起 PG+Redis 容器 + 本地 uvicorn --reload
```

---

## 三 · 改代码前后各做一件事

**改之前**：先读与那个模块**同目录**的 `specs/<模块>.md` ——
⭐ **最该读的是它第三节「⚠️ 看代码会误判的地方」**，
前两节读代码也能推出来，**只有那一节推不出来**。

**改之后**：把那份 spec **同步更新**（新增模块 ⇒ 必须同时建 spec）。

---

## 四 · 跑测试

```bash
# 离线那套（⛔ 不需要 Postgres、不需要外网）—— 这是 CI 跑的那一条
cd app && ../venv/bin/python -m pytest . -m "not integration and not needs_db" -q

# 要真 Postgres 的那套（⚠️ 它会真写 documents 表）
POSTGRES_DB=rag_test POSTGRES_HOST=127.0.0.1 ../venv/bin/python -m pytest . -m needs_db -q

# 要外网 + 真模型的端到端那套
../venv/bin/python -m pytest . -m integration -q
```

> ⚠️ **`app/` 就是 `sys.path` 的根** ⇒ 代码里的导入写 `from core.config import …`，
> ⛔ **不是** `from app.core…`。

---

## 五 · 提交前

```bash
git add -A
git commit          # 写清"改了什么 + 为什么"
```

> 🔴 **⛔ 别把任何凭据提交进去** —— `.env` / `*.key` / `*.pem` 都在 `.gitignore` 里，
> 但**注释和文档里的明文**它挡不住。自查一条：
> `git diff --cached | grep -nE 'sk-|password|secret'`

---

## 六 · 几条这个项目的**硬约定**

| 约定 | 为什么 |
|---|---|
| 🔴 **身份用显式形参贯穿**（`user_id` / `thread_id` 都是**必填关键字**） | 有个默认值就等于"可以忘记传"，而漏传的后果是**静默**的 |
| 🔴 **成本闸的桶必须与账本的桶一致** | 记账落一个 `thread_id`、闸查另一个 ⇒ 闸会**恒看到 0**（形同虚设）|
| 🔴 **带过滤的向量检索必须先开 `iterative_scan`** | 近似索引**先扫索引、后套 `WHERE`** ⇒ 会**静默少于 `LIMIT`** |
| 🔴 **工具执行⛔ 不在应用进程里** | `execute_python` 走**独立容器**，应用不碰 `docker.sock` |
| ⚠️ **文档里写「目标」就标「目标」** | 本仓不把目标写成指标 |

---

## 七 · 许可

**MIT** —— 见 [`LICENSE`](LICENSE)。
