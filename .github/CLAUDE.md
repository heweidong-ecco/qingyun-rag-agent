# `.github/` —— CI 与模板

## 📇 本目录索引
| 条目 | 是什么 |
|---|---|
| `workflows/ci.yml` | ⭐ **CI 的全部**（两个 job：`syntax` · `offline-tests`） |
| `PULL_REQUEST_TEMPLATE.md` | PR 模板（含「改 Prompt/工具/记忆 ⇒ 附 eval 回归」那张表） |

## 🔴 本层特有的规矩
- 🔴 **`ci-local.sh` 逐字执行 `ci.yml` 里那块 `run`** ⇒ 改 `run` 块时**两边自动同步**，
  ⛔ 但**新开 step 就跑不到了**（`.specs` 那条注释写着这个约束）
- 🔴 **两个 job 都必须带 `timeout-minutes`**（有专门的 CI 形状门钉它）
- ⚠️ **CI 里没有 `.env`、没有 `.secret-denylist`** ⇒ 凭据门会自动降级为**部分覆盖**，
  结论行会写明覆盖了哪几节

## 📍 往上读
- 仓库根 `CLAUDE.md`
