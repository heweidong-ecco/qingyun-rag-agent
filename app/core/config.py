"""
集中配置管理
所有环境变量在此读取，其他模块从本文件导入。
敏感信息禁止设默认值，启动时强制校验缺失。
"""
import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件（注意路径关系）
# 如果 config.py 放在 app/ 下，需加载上一级的 .env
load_dotenv(
    os.path.join(os.path.dirname(__file__), '..', '..', '.env')
)

# 判断是否在 Docker 环境中运行
# 本地开发时覆盖为 localhost
IS_DOCKER = os.getenv("DOCKER_ENV", "false").lower() == "true"

TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"

# ==================== 非敏感配置（有合理默认值） ====================
# 数据库
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_DB = os.getenv("POSTGRES_DB", "rag_db")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# JWT 有效期
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# 登录用户名（非敏感，有合理默认值）
LOGIN_USER_NAME = os.getenv("LOGIN_USER_NAME", "admin")

# ==================== 敏感配置（禁止默认值，缺失则拒绝启动） ====================
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
# 登录口令 —— 从 app/access/auth.py 的硬编码字面量迁出（那曾是公开仓库上的活凭据），
# 决策。缺失则拒绝启动（见下方 validate_config）。
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD")
# 可选：**设了才存在** test_user 账号（PREMIUM 角色，用于验证非 admin 的配额/预算路径）。
# 不设 = 该账号不存在 —— fail-closed，不留任何默认口令。
TEST_USER_PASSWORD = os.getenv("TEST_USER_PASSWORD")

# ==================== 生成/对话 LLM —— 端点 = DeepSeek（作者裁定：不再用百炼） ====================
# embedding 固定走 DASHSCOPE(**端点**不变);它的**模型名**已收拢到下面「Embedding」那一节。
# 默认值已改 DeepSeek;`LLM_API_KEY` **不再回落 `DASHSCOPE_API_KEY`**(旧写法会把 embedding 的 key 拿去请求 DeepSeek 端点 —— 静默错配)。
LLM_API_KEY    = os.getenv("LLM_API_KEY")
LLM_BASE_URL   = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "deepseek-v4-flash")
LLM_MODEL_CHAT = os.getenv("LLM_MODEL_CHAT", "deepseek-v4-flash")

# ==================== Embedding —— 端点 = 阿里云百炼 DashScope ====================
#    （第二处写死见 `agent/memory_store.py` 的 mem0 embedder）。变量名由作者定为 `EMBEDDING_MODEL_MAIN`。
# **改这里必须连【维度】一起改**（§2.1：模型名 + 维度 + 表定义 = 三件套）——
#    只把模型名变可配 ⇒ 维度对不上；而**最危险的是"维度恰好相同"**：不报错，但
# 🔴 **（B 段）默认值改成新模型** —— 原句是
EMBEDDING_MODEL_MAIN = os.getenv("EMBEDDING_MODEL_MAIN", "qwen3.7-text-embedding-flash")

#: 🔴 单文件上传的**字节**上限（`①b`  ·  §二·4.1 反推）。
#: **它是反推的，⛔ 不是拍的**：起点是"每次上传 1 万 token"这个额度 ——
#:    汉字 3 字节、汉字 ≈ 0.7 token ⇒ 1 万 token ≈ **4.2 万字节 ≈ 42 KB**。
#: ⚠️ 取 **48 KB** 留一点余量：42 KB 本身是**估算**，卡太死会把正常的 42 KB 文件也拒掉。
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 48 * 1024))

#: 🔴 单次上传切出来的**块数**上限（`①b`  ·  §二·4 第 3 条）。
#: 🔴 **它与上面那个字节上限是【两个量纲】，⛔ 别读成重复**（原文）：
#:    · **字节 / token** 决定**花多少钱**；
#:    · **块数**决定**调多少次 embedding** ⇒ 在 **2 vCPU** 上决定**耗时 / 超时**。
MAX_INGEST_CHUNKS = int(os.getenv("MAX_INGEST_CHUNKS", 200))


#: ：**改过名的额度键 → 现在该用的新键**。
#:
#: 日额度按【池】分开之后（`LLM_DAILY_TOKEN` / `EMBEDDING_DAILY_TOKEN`），
#: 旧的 `DAILY_TOKEN_FREE` / `_PREMIUM` / `_ADMIN` **不再被任何地方读**。
_RENAMED_TOKEN_KEYS = {
    "DAILY_TOKEN_FREE": "DAILY_TOKEN_LLM_FREE / DAILY_TOKEN_EMBEDDING_FREE",
    "DAILY_TOKEN_PREMIUM": "DAILY_TOKEN_LLM_PREMIUM / DAILY_TOKEN_EMBEDDING_PREMIUM",
    "DAILY_TOKEN_ADMIN": "DAILY_TOKEN_LLM_ADMIN / DAILY_TOKEN_EMBEDDING_ADMIN",
}


def renamed_token_keys_present() -> list:
    """哪些**改过名**的额度键还在环境里（现在没人读它们了）。**纯函数 ⇒ 可单测**。

    **抽成函数的理由**：让判据**不依赖其他必需项是否齐全**就能跑
    （否则测它得先把 5 个必需变量都凑齐 —— 那种测试**很脆**）。
    """
    return sorted(k for k in _RENAMED_TOKEN_KEYS if os.getenv(k))


def validate_config():
    """启动时调用，检查敏感配置是否存在"""
    missing = []
    if not DASHSCOPE_API_KEY:
        missing.append("DASHSCOPE_API_KEY")
    if not LLM_API_KEY:
        # 实际上先炸的往往是 **import 期**的 `make_llm`，而不是这里
        #    —— 这条的价值是"政策写下来 + 换导入顺序后仍有人拦"。
        #    **点名它**的 `EnvironmentError`（改前是把 `None` 递给 `ChatOpenAI` ⇒ SDK 那句通用话，
        #    而那句提的 `OPENAI_API_KEY` 本仓根本不用）。**时机没变**：仍在 import 期。
        missing.append("LLM_API_KEY")
    if not POSTGRES_PASSWORD:
        missing.append("POSTGRES_PASSWORD")
    if not JWT_SECRET_KEY:
        missing.append("JWT_SECRET_KEY")
    if not LOGIN_PASSWORD:
        missing.append("LOGIN_PASSWORD")

    if missing:
        raise EnvironmentError(
            f"以下必需的环境变量未设置，请检查 .env 文件或系统环境变量:\n"
            f"{', '.join(missing)}"
        )

    #    逻辑在下面的纯函数里（那样**不依赖别的必需项**就能单测）。
    stale = renamed_token_keys_present()
    if stale:
        raise EnvironmentError(
            "以下环境变量【已改名】，现在不再被读取 —— 留着它们会让额度【静默】变成别的数：\n"
            + "\n".join(f"  {k}  ⇒  拆成了 {_RENAMED_TOKEN_KEYS[k]}" for k in stale)
            + "\n⇒ 把旧键从 .env 里删掉，改成上面的新键（两个池各一份）。"
        )

# config.py 末尾添加
DB_MIN_CONN = int(os.getenv("DB_MIN_CONN", "2"))
# ⚠️ 默认值**写 20**，与 `.env` / `.env.example` 一致（统一）。
#    原先代码写 `30` 而两处 `.env` 写 `20` —— **三处三个数**，
#    实际生效的永远是 `.env` 那个（20）⇒ 代码默认只在"没设 env"时才轮到，
#    而它跟另外两处说的是**两件不同的话**。判据：`grep -rn DB_MAX_CONN .env .env.example app/core/config.py`
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", "20"))

# ==================== 本地开发覆盖 ====================
if not IS_DOCKER:
    POSTGRES_HOST = "localhost"
    REDIS_HOST = "localhost"