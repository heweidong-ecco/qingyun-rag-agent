# `app/eval/` —— 离线评估

> 📇 **⛔ 不在服务路径上** —— 这是 RAGAS 那套**离线**评测，跑在开发机上。

## 📇 本目录索引
| 条目 | 是什么 |
|---|---|
| `evaluate_with_ragas.py` | RAGAS 评测脚本（**有 `__main__`**）· ⚠️ 它**不 import 任何本仓模块**（纯 HTTP + 第三方） |
| `eval_dataset.json` | 评测集 |
| `bad_cases.md` | **2026-06-28 的评测快照** —— ⛔ **不是待办清单** |
| `ragas_report.json` · `ragas_detailed_report.json` | 两份历史报告 |

## 🔴 本层特有的规矩
- 🔴 **用【裸文件名】读同目录文件**（`open("eval_dataset.json")`）
  ⛔ **整包搬动没问题，单独搬一个文件就会读不到**（静默失败）。
  正确跑法：`cd app/eval && python evaluate_with_ragas.py`
- ⚠️ **`ragas` / `datasets` 不在 `app/requirements.txt` 里**（构建期被裁）
  ⇒ 本机的 venv 里也没装 ⇒ 要跑得先装。
- ⚠️ **`bad_cases.md` 是快照**：它记的是"那个时候系统表现如何"，⛔ **不用来回答"现在还剩什么没做"**。

## 📍 往上读
- `../CLAUDE.md`（`app/`）· 仓库根 `CLAUDE.md`
