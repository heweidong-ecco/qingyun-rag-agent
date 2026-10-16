"""
Locust 性能基准测试脚本 V2
模拟真实用户多轮对话行为，覆盖检索管线的各个环节。
"""
from locust import HttpUser, task, between
import random
import os
from dotenv import load_dotenv

# 从仓库根目录的 .env 读登录口令（locust 按 CLAUDE.md 的用法从仓库根启动）
load_dotenv()

TEST_QUESTIONS = [
    "Python 编程的特点是什么？",
    "Docker 能解决什么问题？",
    "机器学习和人工智能的关系",
    "PostgreSQL 有哪些特性？",
    "向量数据库在 RAG 中的作用",
    "苹果公司的主要产品",
    "2025年全球AI市场规模",
    "LangChain 是什么？",
    "FastAPI 的性能优势",
    "Redis 有哪些用途？",
]

FOLLOW_UP_QUESTIONS = [
    "它的性能怎么样？",
    "那和别的技术比呢？",
    "能举个具体的例子吗？",
    "为什么这样设计？",
    "有什么局限性？",
]

class RAGAPIUser(HttpUser):
    """模拟真实 RAG 用户：初次提问 + 可能追问"""
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

    # 本文件四个 task 在 2026-09-16 统一修正）：
    #  · `mode` 是**查询参数**，不是 body 字段 —— api_v1_rag.py 把它独立声明为 query 参数，
    #    **遮蔽了** body 里的 QuestionRequest.mode。原先放在 json 里 ⇒ **被静默忽略**，
    #    所有请求实际都走默认的 accurate_norerank ⇒ **各 task 的 name 标签全是假的**。
    #  · 取值统一为 `accurate_norerank`（= 修复前的**实际**行为），以保证与历史基线可比。
    #    若日后要压 `accurate`（开 Cross-Encoder 重排），那是**口径变更**，须重跑基线并单独说明。
    @task(60)
    def basic_search(self):
        """基础检索 —— 最轻口径：仅查询改写，**不重排、不生成答案**"""
        self.client.post(
            "/api/v1/rag/search",
            headers=self.headers,
            params={"mode": "accurate_norerank"},
            json={
                "question": random.choice(TEST_QUESTIONS),
                "top_k": 3,
            },
            name="/rag/search (基础检索/norerank)"
        )

    @task(20)
    def search_with_answer(self):
        """检索 + 生成答案"""
        self.client.post(
            "/api/v1/rag/search",
            headers=self.headers,
            params={"mode": "accurate_norerank"},
            json={
                "question": random.choice(TEST_QUESTIONS),
                "top_k": 3,
                "generate_answer": True,
            },
            name="/rag/search (生成答案/norerank)"
        )

    @task(10)
    def search_with_citations(self):
        """检索 + 带引用的答案"""
        self.client.post(
            "/api/v1/rag/search",
            headers=self.headers,
            params={"mode": "accurate_norerank"},
            json={
                "question": random.choice(TEST_QUESTIONS),
                "top_k": 3,
                "generate_answer": True,
                "citations": True,
            },
            name="/rag/search (带引用/norerank)"
        )

    @task(10)
    def multi_turn_search(self):
        """模拟多轮对话：先问一个问题，再追问"""
        question = random.choice(TEST_QUESTIONS)
        follow_up = random.choice(FOLLOW_UP_QUESTIONS)

        # 第一轮：基础检索
        self.client.post(
            "/api/v1/rag/search",
            headers=self.headers,
            params={"mode": "accurate_norerank"},
            json={"question": question, "top_k": 3},
            name="/rag/search (多轮-第一轮/norerank)"
        )

        # 短暂思考
        import time
        time.sleep(1)

        # 第二轮：带历史的追问
        # `conversation_history` 的类型是 **`list[dict]`**（schemas.py:QuestionRequest），
        #    原写法 `[f"用户: {question}"]` 传的是 `list[str]` ⇒ Pydantic 校验失败 **422**。
        #    改成 schema 声明的形态后，还有第二关：`query_rewriter.py` 会对历史做
        #    `"|".join(history[-5:])` —— 收到 dict 会 TypeError ⇒ **500**。本次一并修了
        #    query_rewriter 的容错（两种类型都吃），否则这里会从 422 变 500。
        self.client.post(
            "/api/v1/rag/search",
            headers=self.headers,
            params={"mode": "accurate_norerank"},
            json={
                "question": follow_up,
                "top_k": 3,
                "generate_answer": True,
                "conversation_history": [{"role": "user", "content": question}],
            },
            name="/rag/search (多轮-追问/norerank)"
        )

    # 新增流式任务
    @task(10)
    def stream_search(self):
        """流式检索 + 生成答案（SSE），观察延迟特征"""
        question = random.choice(TEST_QUESTIONS)
    
        # 使用 catch_response=True 来手动处理流式响应
        with self.client.post(
            "/api/v1/rag/stream_search",
            headers=self.headers,
            json={
                "question": question,
                "top_k": 3,
                "citations": False,   # 可改为 True 测试带引用的流式
            },
            stream=True,
            catch_response=True,
            name="/rag/stream_search (流式)"  # ← 添加这一行
        ) as response:
            if response.status_code == 200:
                # 模拟客户端消费流：读取全部数据直到结束
                # 注意：Locust 会测量整个过程的总时间
                for chunk in response.iter_content(chunk_size=None):
                    pass
                response.success()
            else:
                response.failure(f"流式请求失败，状态码: {response.status_code}")