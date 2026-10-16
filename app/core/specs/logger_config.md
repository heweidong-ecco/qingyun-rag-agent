# `app/core/logger_config.py`

## ✅ 做了什么

配置 **Loguru**：控制台 + 文件双写，`_LOG_DIR` 指向 **`app/logs/`**。

```python
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
```

## 🟡 做到哪 / 缺什么

- 被 `app/main.py` 引用（**2 处**）—— 服务启动时调 `setup_logger()`。
- ⚠️ **零测试**（`git grep -l logger_config -- app/tests` ⇒ **0 个文件**）。
- 日志按天滚动、30 天清理（运维约定见 `docs/说明/运维.md`）。

## ⚠️ 看代码会误判的地方 ⭐

1. 🔴 **`_LOG_DIR` 的层数是【改过的】**（2026-10-07）：改前是**相对 CWD** 的 `"logs"`，
   ⇒ 从 `app/` 起 uvicorn 日志进 `app/logs/`，**从仓根起就进 `<仓根>/logs/`**
   —— **本机两个目录都真的存在过**（实测 `ls -d logs app/logs`）。
   ⇒ **看到仓库根有个 `logs/`，⛔ 别以为它还在被写**（那是改前的遗留）。
2. 🔴 **2026-10-09（模块化重构）这里换过一次 `.parent` 层数** ——
   本文件从 `app/` 挪进 `app/core/` ⇒ `parent` 改成 `parent.parent`。
   ⛔ **再挪动它，必须同步改这一行**，否则日志**静默**写到别的目录（不报错）。
   📌 判据：`python -c "import core.logger_config as l; print(l._LOG_DIR)"` ⇒ 必须是 `<仓根>/app/logs`。
3. ⚠️ **2026-09-20 修过一处"注释与代码不符"**：原注释写「JSON 格式」，而下面的 `format=` 并不是
   —— ⇒ 读本文件时**以代码为准**，⛔ 别信它上面那句概述（本仓同族已记多次）。

## 关联

- `docs/说明/运维.md` —— 日志位置与保留期的运维口径
- `app/core/specs/config.md` —— 日志级别等开关的来源
