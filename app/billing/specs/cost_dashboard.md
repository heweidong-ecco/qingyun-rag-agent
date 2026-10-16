# `app/billing/cost_dashboard.py`

## ✅ 做了什么

**成本统计可视化面板**（Gradio）：5 个格子 + CSV 导出。
挂在 `app/main.py` 上，由环境变量（`ENABLE_DASHBOARD` 一类）**门控**。

## 🟡 做到哪 / 缺什么

- **9 处产品代码引用 · 1 个测试文件提到**。
- 🔴 **Gradio 在导入期就建 `Blocks`** ⇒ `app/tests/conftest.py` 里那句
  `GRADIO_ANALYTICS_ENABLED=False` 是**为它设的**（不设会卡住进程退出）。
- ⚠️ 面板的**对外正式版**是前端那个 `GET /cost` 页；本文件是**另一条并行的可视化路径**。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **5 个格子的口径【不是同一套】，读的时候别串**（2026-10-03 实跑核过）：
   | 格 | 口径 |
   |---|---|
   | 前 4 格 | **本人**口径 |
   | 第 5 格（2026-10-03 ·  新增） | 🔴 **全站** |
   ⛔ **把全站那个数当成"我的用量"** —— 这是本文件最容易被误读的地方。
2. 🔴 **`get_purpose_summary()` 是【进程内存】、且【不分用户】** ——
   ⇒ 它给出的"调用统计"**重启即清零**，且**不是某个人的**。
   ⚠️ 页面上的**口径徽标**就是为区分这件事而加的（§三）。
3. ⚠️ **2026-09-20 删过一处重复的 `import os`**（文件头已有一处）——
   ⇒ pyflakes 报 `redefinition`。与本仓 `db.py` 的 `get_db()` 那族**同型**（同名定义之后者覆盖前者）。
4. ⚠️ **`matplotlib.use('Agg')` 必须在 `import pyplot` 之前** ——
   ⛔ 调换顺序会在**多线程**下出问题（本文件顶部那行注释就是在说这个）。
5. ⚠️ **它是 Gradio 应用，不是 FastAPI 路由** ⇒ 改它**不影响 `/docs`**。

## 关联

- `app/billing/specs/token_tracker.md` —— 5 个格子的数据来源（各格口径的**权威**）
- `app/static/specs/static_frontend.md` —— **面向访客的**那个 `GET /cost` 页（两者不是一回事）
-  —— 口径徽标与"三条有意不露"
- `app/tests/conftest.py` —— `GRADIO_ANALYTICS_ENABLED` 那行注释
