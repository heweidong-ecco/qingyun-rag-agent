# 用户级 skills · 必用表（A 级 9 · B 级 5）

> 🔴 **2026-10-09**：这一段**原在仓库根 `CLAUDE.md`**，
> 是**每次会话都加载**的。它属于**参考性内容**（查表用的），不是"每次都必须读的规矩"
> ⇒ **搬到这里**，根里只留**摘要 + 指针**。
> ⚠️ **判据没放宽** —— 下表仍是**命中必须用**；只是**加载时机**变了。

> ⚠️ **目录里的总数【别写死】** —— 跑 `ls -d ~/.claude/skills/*/`（**会变**）。
> **本表列的只是其中一部分**，且**不全是 superpowers 的**
> （`agent-system-creator` / `gate-review` / `new-project-launch` / `留痕-checks` 是**自建**的）。
> ⬜ **表外还有几个【未定级】**：`agent-system-creator` · `gate-review` · `grilling`
> `new-project-launch` · `skill-creator` · `留痕-checks`
> （⚠️ **`留痕-checks` 虽未进表，但在下面被单独讨论** —— 它有本仓适配裁决，见「冲突与例外」）。

> **用户指令（2026-09-17）**：「使用用户级中 superpowers 的 skills……**如果命中必须用**，
> 先写好，过程中我再动态调整。」**本表由体检定级** ⇒ `docs/skill-适配体检-2026-09-17.md`。

### ⛔ 第一条：**知道 skill 存在 ≠ 会用 skill**

每次会话我自动收到**所有 skill 的名字 + 一行描述**，但那**只是索引** ——
**只有调用 `Skill` 工具，正文才会进入上下文。没调用 = 这个 skill 等于不存在。**

> **实测佐证（本仓）**：此前 **8 个 PR 一次都没跑过 `/留痕-checks`**，而我每遍读 PR 纪律时
> 都"知道"它存在 —— 。**门挂在别处，就等于没有门。**

### A 级 · **命中必须用**（9 个 —— 与我们的工作流一致）

| 触发场景 | 必须用 | 原文判据 | 代价 |
|---|---|---|---|
| **任何 bug / 测试失败 / 异常行为**，提出修法之前 | `systematic-debugging` | before proposing fixes | ~10k |
| **实现任何功能或修 bug**，写实现代码之前 | `test-driven-development` | before writing implementation code | ~4k |
| **要说"做完了 / 修好了 / 过了"之前**，或提交 / 开 PR 之前 | `verification-before-completion` | **evidence before assertions always** | ~0.9k |
| 有 spec/需求要做多步任务，**动代码之前** | `writing-plans` | before touching code | ~2.2k |
| 已有书面计划要执行（带 review 检查点） | `executing-plans` | with review checkpoints | ~0.6k |
| 完成一个任务 / 大功能 / **合并之前** | `requesting-code-review` | | ~2.2k |
| **收到** code review 意见时（尤其看不懂或不认同的） | `receiving-code-review` | 要**技术较真与验证**，不是表演式认同、不是盲目照做 | ~1.6k |
| 实现完成、测试全过，要决定**怎么合入** | `finishing-a-development-branch` | | ~1.9k |
| **任何创造性工作之前**（加功能 / 建组件 / 改行为） | `brainstorming` | MUST use before any creative work | **~20k** ⚠️ |

> ⚠️ **`brainstorming` 是表里最贵的（≈20k tokens）** —— 它挂在"任何创造性工作之前"，
> 意味着**每个功能请求都会拉 20k**。本机上下文 1M 扛得住，但**要知道它是笔开销**。
> 🔴 **这一列「代价」是会过期的实测值** —— 来自 `docs/skill-适配体检-2026-09-17.md`，
> **skill 改版后要重量**，⛔ 别当永久值用。

### B 级 · `⬜ 待适配`（5 个 —— **它假设的工作流与我们不同**，用前先判）

| skill | 为什么待适配 | 我们的实际 |
|---|---|---|
| `using-git-worktrees` | 假设需要 worktree 隔离 | **在本仓直接干、一分支一 PR**；worktree 不是我们的模式 |
| `dispatching-parallel-agents` | 假设并行派发多 agent | **单人逐条确认** |
| `subagent-driven-development` | 假设用子 agent 执行计划 | 同上，且我们的规矩是「**改动等发话**」 |
| `writing-skills` | 假设常写 skill（且 **~26.8k**，表里最贵） | 极少写 |
| `using-superpowers` | ⚠️ **它的核心主张与本仓纪律冲突**（见下） | 只采纳它的「名字≠skill」，**不采纳"1% 就必须调用"** |

> ⚠️ **`using-superpowers` 的冲突点**：它主张「只要有 **1% 可能** skill 适用就**绝对必须**调用」，
> 且**任何**回复或动作**之前**都要先调用（包括提问、看代码、查文件）——
> **这与本仓「非必要的不要做」「改动等发话」正面冲突。**
> ✅ 好在它自己写了：`User instructions (CLAUDE.md …) take precedence over skills`
> ⇒ **以本仓 `CLAUDE.md` 为准**，那条"1% 就必须调用"**不采纳**。
> 📄 原文出处 ⇒ `docs/skill-适配体检-2026-09-17.md`

### 冲突与例外 —— **必须说出来，不许静默跳过**

1. **同场景命中多个** ⇒ 按上表**从上到下**依次执行
2. **与项目 `CLAUDE.md` /  冲突** ⇒ **以项目规矩为准**，并**写明冲突点**
3. **判据在本仓对不上** ⇒ ⛔ **不许含糊通过**：有替代判据 ⇒ 标 `⬜ 待适配`；**没有 ⇒ 标 `⛔ 无法执行`**

> 📄 **已知不适配 + 本仓适配裁决**（`留痕-checks` 查 8 项、本仓只 5 项适用）
> ⇒ 「PR 纪律」段 · `docs/规则草稿-规则必须绑定路径.md`

