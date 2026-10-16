# `app/tests/` —— pytest 用例

> 📇 **本层 = 索引表 + 主要内容**。用例**按"被测模块"命名**，⛔ 不按测试类型。
>
> 🔴 **用例数 / 文件数一律现跑，⛔ 不写死**（本行原先写着「94 个」，早就不是了）：
> ```bash
> POSTGRES_DB=rag_test POSTGRES_HOST=127.0.0.1 venv/bin/python -m pytest app/tests/ --collect-only -q -p no:cacheprovider | tail -2
> ls app/tests/test_*.py | wc -l
> ```

## 📇 本目录索引

**跑法**（⛔ 从**仓库根**跑）：
```bash
./venv/bin/python -m pytest app/ -m "not integration and not needs_db" -q
```
- **`app/conftest.py`**（在**上一层**）是 pytest 根 conftest —— ⭐ 它一留，`app/` 就在 `sys.path` 上
  ⇒ 本目录的用例才能用**裸导入**（`from main import app`）

**命名规律**（照它找用例，比把文件逐个列一遍有用）：
| 前缀 | 测什么 |
|---|---|
| `test_<模块>.py` | 那个模块本身 |
| `test_<模块>_wiring.py` | 🔴 **接线**：那个能力**真的被调用链用上了吗**（本仓最多的一类判据） |
| `test_<页面>_page.py` | 前端页面（`app/static/web/*.html`） |
| `test_{auth,budget,breaker,session}*.py` | 几族横切能力 |

**两个 marker**（`app/pytest.ini` 里定义，**都不进 CI**）：
- `integration` —— 要真 Postgres + DashScope 外网
- `needs_db` —— 只要真 Postgres（**本机跑必须带 `POSTGRES_DB=rag_test`**）

## 🔴 本层特有的规矩

1. 🔴 **⛔ 不许空跑**：凡是"遍历某集合再逐个断言"的用例，**必须带防空跑断言**
   （本仓多条前科：`glob("*.py")` 扫到 0 个 ⇒ **空集合断言恒为真**）。
   ⇒ 本目录里有 `_product_py()` 这样的共享助手，**它自带防空跑**，用那个。
2. 🔴 **改了别的模块 ⇒ 全量跑**：`F401 ≠ 死导入`、`grep 也兜不住按属性取的调用` ——
   本仓为此红过多次。
3. ⚠️ **拿真实服务当判据的用例**（`test_mcp_*`）**会真起子进程** ⇒ 偶尔抖动，
   单跑绿、复跑绿即可判为抖动（有登记）。
4. 🔴🔴 **⛔ 别用「子进程里 import `main`」当判据**（2026-10-10 实测）。
   ⚠️ `main` 一被 import 就拉起 `agent.memory_store` ⇒ `mem0` ⇒ 一个**本地 Qdrant 单实例锁**；
   而 **pytest 这个会话本身已经 import 过 `main`** ⇒ **父进程攥着那把锁**
   ⇒ 第二个进程当场死在
   `RuntimeError: Storage folder … is already accessed by another instance of Qdrant client`。
   🔴 **实测过**：**同一条子进程命令，单独跑成功、在 pytest 里跑必失败**。
   ⇒ 要判"某个入口的路由表里有没有某条路由"，**走同进程**（`main.app`），
     ⚠️ 并**显式断言"本进程没 import 过另一个入口"** —— 那类 import 会**就地改掉 `main.app`**。
   📄 例 ⇒ 一条已下线的旧用例（文件头写清了它是怎么绕开的）

## 📍 往上读
- `../CLAUDE.md`（`app/`）· `docs/说明/测试.md` · 仓库根 `CLAUDE.md`
