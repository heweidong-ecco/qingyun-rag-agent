# `locust/` —— 压测

> ## 口径与数据 ⇒ **不在这里**
>
> **压测的口径（要什么 · 约束 · 环境 · 数字 · 哪几档不能用 · 为什么）全在**
> ⭐ **`locust/压测口径与基线-20261017.md`**。
> ⭐ **结论**（5 分钟读完）⇒ **`locust/结论-压测-20261017.md`**
> ⭐ **原始过程数据**（每档的 log/CSV/每秒时间序列）⇒ **`locust/结果-20261017/`**（那份 README 是逐档索引）
> ⇒ 本文**只留【怎么跑】**，⛔ 别在本文再写一份口径（本仓前科：同一件事写两处 ⇒ 必然有一处先过期）。

> 📌 **这是什么**：**放在仓库里给来看代码的人看的、固定环境下的压测负载数据**。
> ⛔ **不是内部调优工具**，⛔ **不跑在 CI 里**。

---

## 一 · 目录里有什么（**两代，别读混**）

>：原 `bench/` **已并进本目录**（作者裁「并进 `locust/`」）。
> ⚠️ 并进来时 `bench/locustfile.py` **改名成 `locustfile_bench.py`** —— 它与仓库根那个 `locustfile.py` **同名**，不改名会撞。

| 文件 | 代 | 契约 |
|---|---|---|
| `locustfile.py` · `locustfile_v2.py` · `locustfile_hybrid.py` | **上一代** | **被 `app/tests/test_locust_payload.py` 守卫着**（那三个名字就写在该测试里） |
| **`locustfile_bench.py`** | **这一代**（`T5-8`） | **作者 裁**「**不进 pytest**」，⛔ 也不进 `requirements.txt` |
| `压测口径与基线-20261017.md` | —— | ⭐ **口径 + 实测基线的唯一来源** |

---

## 二 · 怎么跑

### 2.1 起环境

```bash
docker ps --format '{{.Names}}' | grep eval    # ⇒ 必须为空（有评测在跑时不许 up）
docker compose up -d api                       # 顺带起 postgres / redis / executor
docker ps --format '{{.Names}}\t{{.Status}}'   # ⇒ qingyun-rag-agent-api-1 healthy
```

### 2.2 拿一把 key（**它 不进仓**）

```bash
# 单把（够压 ≤3 并发）
export RAG_API_KEY="$(grep '^ISOLATION_A_API_KEY=' .env | cut -d= -f2- | tr -d '"'"'"' ')"

# 或者一组（推荐 —— 上限 ≈ 3 req/s × key 数）。建 key 见口径文档 §八：
#   ./venv/bin/bench1 --days 7 --out <仓外凭据目录>/bench-key-1.key
export RAG_API_KEYS="$(cat 路径1),$(cat 路径2),$(cat 路径3)"

export RAG_BASE_URL=http://localhost:8000
```

⚠️ **鉴权头是 `X-API-Key`**（见 `app/main.py`）—— ⛔ **别用 `Authorization`**。
⚠️ **两把以上的 key 会落在两个限流桶里** —— 这件事**直接决定你能压到几并发**，
理由与实测见口径文档 §五。

### 2.3 跑

```bash
# ① 免费档（默认）—— 零花费，可反复跑
./venv/bin/locust -f locust/locustfile_bench.py --headless -u 3 -r 1 -t 30s --only-summary   # 单把 key
./venv/bin/locust -f locust/locustfile_bench.py --headless -u 12 -r 3 -t 60s --only-summary  # 三把 key

# ② 付费档 —— 🔴 真调 embedding + 改写 LLM，**每次请求都花钱**
BENCH_PAID=1 ./venv/bin/locust -f locust/locustfile_bench.py --headless -u 9 -r 3 -t 60s
```

⚠️ **用 `./venv/bin/locust`**（本仓的 locust 装在 `./venv`），⛔ **别用系统 `python3` / `locust`**。

---

## 三 · 并发档位：**按 实测**（不再是估的）

**上限 ≈ 3 req/s × key 数** —— 一把 key 一个限流桶（`rate_limit:{user_name}`）。

| 配置 | 持续干净上沿（≥5 分钟） | 过了会怎样 |
|---|---|---|
| **1 把 key** | **3 并发**（≈3 req/s） | 5 并发 **10.29 %** 失败 · 10 并发 **47.94 %** · 全是 429 |
| **3 把 key** | **9 并发**（8.9 req/s · 2662 请求 0 %） | 12 并发跑 5 分钟 **10.60 %**（30 秒的档看不出来 —— 被突发容量骗） |

🔴 **根因不是机器**（10 并发时 `qingyun-rag-agent-api-1` 只用 **408 MiB / 3.84 GiB**、CPU **11 %**），
**是按 `user_name` 的令牌桶先到顶**。
⇒ **旧文那张「10 = 主档」是没跑过的猜测，已作废。**
📄 算术、判据、**其他几层硬限制**（全局桶 / 会话额度 / 全站熔断 / 用户日额度）、付费档数据
⇒ ⭐ **`locust/压测口径与基线-20261017.md`** §四 / §五

---

## 四 · 结果放哪

**原始数据 ⇒ 本目录下按日期建一个目录**（现有 `locust/结果-20261017/`，那份 README 是逐档索引）；
**结论 ⇒ `locust/结论-压测-20261017.md`** 那样单独一份。
⚠️ **口径、环境、读数警告的写法 ⇒ 照抄口径文档 §二/§三/§四 那几张表**，⛔ **别只贴数字**。
⚠️ **新跑一次就新开一个日期目录**，⛔ 别覆盖旧的（旧数据是"当时试过什么"的证据）。

---

## 五 · 不做什么

| 不做 | 为什么 |
|---|---|
| 写进 `app/requirements.txt` | 会进镜像 ⇒ 给交付物增肥 |
| 进 `app/test_*.py` / pytest | 作者裁定 |
| 写进 CI | 同上；且 CI 没有 postgres 全栈 |
| 删掉 `archive/` 里那份旧基线 | 它记着"当时试过什么"——**留档不删** |

---

## 六 · 旧基线为什么不能用（一句话）

⇒ 作者裁「**旧的不要**」⇒ 已移到**仓根的** `archive/`（⛔ **没删**）。
📄 完整理由 ⇒ `locust/压测口径与基线-20261017.md` §九
