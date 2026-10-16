# `.github/workflows/` —— workflow 文件

## 📇 本目录索引
| 文件 | 是什么 |
|---|---|
| `ci.yml` | ⭐ **CI 的全部**（`syntax` + `offline-tests` 两个 job） |
| `codeql.yml` | SAST。🔴 **只在【公开仓】跑** —— 私有仓的代码扫描要付费（`if: !github.event.repository.private`）|

## 🔴 本层特有的规矩
- ⚠️ **每个 job 必须带 `timeout-minutes`** —— ⛔ 「卡住」与「跑得很慢」在机器痕迹上**一样**，
  没有上限时"卡住"不是一个**显式状态**
- ⚠️ **`fetch-depth: 0` 是必须的** —— 凭据门用 `<base>...<head>`（三点）需要 merge-base
- ⚠️ **这一块 `run:` 会被 `scripts/ci-local.sh` 逐字读出来执行** ⇒ ⛔ 别写 GitHub 表达式

## 📍 往上读
- `../CLAUDE.md` · 仓库根 `CLAUDE.md`
