# Security Policy

本仓是一个**个人项目**（FastAPI + RAG + Agent 的演示服务），单人维护。
这份文件说明三件事：**怎么报安全问题** · **支持哪些版本** · **本项目在安全上做了什么、明确不做什么**。

---

## Reporting a Vulnerability

**请不要用公开的 GitHub issue 报安全问题。**

请用 GitHub 的私密通道：本仓 **Security** 标签页 → **Report a vulnerability**
（`https://github.com/heweidong-ecco/qingyun-rag-agent/security/advisories/new`）。

报告里请尽量带上：

- 问题类型与影响（能造成什么后果）
- 受影响的文件与位置
- 复现步骤；能做到的话，附一个最小复现
- 你的利用思路或 PoC

**响应预期**：这是个人项目，没有 SLA、没有值守。我会尽快确认；修复可用之后再公开披露。

---

## Supported Versions

本仓没有发布版本标签，只有一条线：

| 分支 | 提供安全修复 |
|---|---|
| `main` | 是 |
| 其他分支、历史提交 | 否 |

修复只进 `main`。

---

## Security Measures

| 措施 | 位置 |
|---|---|
| `.env` 不入库，凭据运行时注入 | `.gitignore` |
| API Key 只存 SHA256 哈希，不存明文 | `app/access/auth.py` |
| 敏感配置不设默认值：缺了拒绝启动 | `app/core/config.py` |
| 凭据门：扫描暂存区的新增行 | `scripts/check_secrets.sh` |
| 历史密钥扫描（gitleaks，扫全历史） | `.github/workflows/ci.yml` |
| 静态分析（CodeQL） | `.github/workflows/codeql.yml` |
| 依赖漏洞扫描（本地 `pip-audit`） | `scripts/check_dep_vulns.sh` |
| 依赖版本就地钉死 | `app/requirements.txt` |
| PostgreSQL 与 Redis 只绑 `127.0.0.1` | `docker-compose.yml` |
| 四层配额：单次 / 会话 / 用户日级 / 全站日级 | 本仓的决策记录（⛔ 不随产品仓提供） |
| JWT：短期 15 分钟、长期 7 天 | `app/core/config.py` |
| 代码执行跑在独立容器，带白名单与时间/输出上限 | `app/tools/code_executor_impl.py` |

威胁模型（资产、信任边界、攻击者、已接受的残余风险）见 `docs/威胁模型.md`。

---

## Scope and Limitations

**这是一个演示项目，不是生产系统。** 明确**不做**的：

- 没有渗透测试，没有第三方安全审计
- 没有漏洞赏金
- 没有 SLA，没有值守响应
- 不承诺抵御针对性攻击

**知情接受的残余风险**（是"接受了"，不是"没发现"）：

- Redis 没有口令 —— 因此它只绑 `127.0.0.1`，不对外
- 角色与配额按账号分档，**不构成多租户强隔离**
- 第三方依赖若出现无法升级的已知漏洞，会在 ` 里写明接受理由，
  并登记到 `docs/待办总表.md`，而不是静默放过

⚠️ **2026-10-15 更正**：原来这里写「演示部署（魔搭创空间）另有一套平台约束」——
**那个平台已弃用**⇒ 那份文档现在**只当当时的实测事实**读（加了取代标记）。

---

## Disclosure Policy

修复进入 `main` 之后再公开讨论。若你希望匿名，请在报告里说明。
