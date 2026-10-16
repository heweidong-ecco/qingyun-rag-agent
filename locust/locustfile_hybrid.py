"""
混合负载压测脚本：模拟用户同时使用 RAG 检索和 Agent 对话
"""
from locust import HttpUser, task, between
import random
import os
from dotenv import load_dotenv

# 从仓库根目录的 .env 读登录口令（locust 按 CLAUDE.md 的用法从仓库根启动）
load_dotenv()

# ==================== RAG 测试数据 ====================
RAG_QUESTIONS = [
    "Python 编程的特点是什么？",
    "Docker 能解决什么问题？",
    "机器学习和人工智能的关系",
    "PostgreSQL 有哪些特性？",
    "向量数据库在 RAG 中的作用",
    "FastAPI 的性能优势",
]

# ==================== Agent 测试数据 ====================
AGENT_QUESTIONS = [
    "今天天气怎么样？",
    "计算 123*456",
    "今天是几号？",
    "帮我搜索最新的人工智能新闻",
    "翻译'你好，世界'成英文",
]

AGENT_FOLLOW_UPS = [
    "它的性能怎么样？",
    "那和别的技术比呢？",
    "能举个具体的例子吗？",
]

# ==================== 用户行为类 ====================
class HybridUser(HttpUser):
    """模拟真实用户：80% 做 RAG 检索，20% 使用 Agent"""
    wait_time = between(1, 3)

    def on_start(self):
        """登录获取 Token

        口令从 `.env` 读（不再硬编码）—— 。
        登录失败必须**显式报错**：原写法只是把 headers 置空，于是整轮压测会跑出
           一堆静默 401 却看不出原因 —— 与 #3 那个"mode 被静默忽略"是同一类坑。
        """
        user = os.getenv("LOGIN_USER_NAME", "admin")
        password = os.getenv("LOGIN_PASSWORD")
        if not password:
            raise RuntimeError(
                "未设置 LOGIN_PASSWORD —— 请在仓库根目录的 .env 里配置"
                "（locust 需从仓库根启动，本文件顶部已 load_dotenv()）"
            )
        response = self.client.post("/api/v1/auth/login", json={
            "user_name": user,
            "password": password,
        })
        if response.status_code != 200:
            raise RuntimeError(
                f"登录失败: HTTP {response.status_code} — {response.text[:200]}\n"
                f"请检查 .env 的 LOGIN_PASSWORD（用户名 {user!r}）"
            )
        self.token = response.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    @task(60)  # 权重 60：RAG 检索
    def rag_search(self):
        """RAG 检索（模拟项目一的使用）"""
        self.client.post(
            "/api/v1/rag/search",
            headers=self.headers,
            # `mode` 是**查询参数**，不是请求体字段 ——
            #    api_v1_rag.py 把它独立声明为 query 参数，**遮蔽了** body 里 QuestionRequest.mode，
            #    所以原先放在 json 里的 mode 会被**静默忽略**，恒走默认的 accurate_norerank。
            #    取值统一为 `accurate_norerank`（= 修复前的实际行为）以保证与历史基线可比 —— 。
            params={"mode": "accurate_norerank"},
            json={
                "question": random.choice(RAG_QUESTIONS),
                "top_k": 3,
            },
            name="/rag/search (基础检索/norerank)"
        )

    @task(10)  # 权重 10：RAG 检索 + 生成答案
    def rag_search_with_answer(self):
        """RAG 检索 + 生成答案"""
        self.client.post(
            "/api/v1/rag/search",
            headers=self.headers,
            params={"mode": "accurate_norerank"},   # 同 rag_search：mode 是 query 参数
            json={
                "question": random.choice(RAG_QUESTIONS),
                "top_k": 3,
                "generate_answer": True,
            },
            name="/rag/search (生成答案/norerank)"
        )

    @task(15)  # 权重 15：Agent 基础对话
    def agent_chat(self):
        """Agent 基础对话"""
        self.client.post(
            "/api/v1/agent/mcp_chat",
            headers=self.headers,
            # 该端点**没有 Pydantic 请求体模型**（签名见 api_v1_agent.py:349-357），
            #    `question` / `thread_id` 都是 **query 参数**。
            #    原先发 json body ⇒ FastAPI 找不到必填 query 参数 ⇒ **必然 422**（占本脚本 25% 权重）。
            #    已核过 app.openapi()：该 path 下 requestBody 不存在、parameters 含 question。
            params={
                "question": random.choice(AGENT_QUESTIONS),
                "thread_id": f"perf-{random.randint(1, 100)}",
            },
            name="/agent/mcp_chat (Agent对话)"
        )

    @task(10)  # 权重 10：Agent 多轮对话
    def agent_multi_turn(self):
        """Agent 多轮对话（先提问，再追问）"""
        question = random.choice(AGENT_QUESTIONS)
        follow_up = random.choice(AGENT_FOLLOW_UPS)
        thread_id = f"perf-multi-{random.randint(1, 50)}"

        # 第一轮（query 参数，见 agent_chat 的说明）
        self.client.post(
            "/api/v1/agent/mcp_chat",
            headers=self.headers,
            params={"question": question, "thread_id": thread_id},
            name="/agent/mcp_chat (多轮-第一轮)"
        )

        # 短暂思考
        import time
        time.sleep(0.5)

        # 追问
        # 这里**刻意不传 `conversation_history`**：
        #    该端点根本没有这个参数（签名见 api_v1_agent.py:349-357），传了只会被忽略；
        #    多轮上下文靠 **同一个 thread_id + 服务端 checkpointer** 维持，客户端不需要携带历史。
        self.client.post(
            "/api/v1/agent/mcp_chat",
            headers=self.headers,
            params={"question": follow_up, "thread_id": thread_id},
            name="/agent/mcp_chat (多轮-追问)"
        )

    @task(5)  # 权重 5：健康检查
    def health_check(self):
        """健康检查"""
        self.client.get("/health", name="/health (健康检查)")