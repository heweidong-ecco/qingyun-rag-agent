"""
集成测试：端到端验证 RAG 完整流程
模拟用户操作：登录 → 上传文档 → 检索 → 验证答案
"""
import pytest
from fastapi.testclient import TestClient
from main import app
from core.config import LOGIN_USER_NAME, LOGIN_PASSWORD

# **整篇标记 `needs_db`** —— 端到端流程会**真写 `documents` 表**（且无 cleanup），
# 而那是 `agent-eval-gate` 评测用的知识库。⇒ **不进 CI**；
# 本机跑时**必须**带库名隔离：`cd app && POSTGRES_DB=rag_test pytest app/ -m "not integration"`。
pytestmark = pytest.mark.needs_db


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """登录并获取 JWT Token

    口令从 `config` 读（= `.env` 的 `LOGIN_PASSWORD`），**不硬编码** —— 。

    这是**第二份**重复的 `auth_headers`（另一份在 `app/conftest.py:11`，行为一致）。
    本次只改口令来源；**是否 dedupe 另记**（已登记在 docs/CODE_INVENTORY.md 的重复项里）。
    """
    response = client.post("/api/v1/auth/login", json={
        "user_name": LOGIN_USER_NAME,
        "password": LOGIN_PASSWORD
    })
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestRAGIntegration:
    """RAG 端到端集成测试"""

    def test_full_pipeline(self, client, auth_headers):
        """
        完整流程测试：
        1. 插入一条包含特定信息的文档
        2. 用相关问题检索
        3. 验证检索结果包含文档内容
        """
        # ===== 第一步：插入测试文档 =====
        doc_content = "公司2025年第四季度营收为5800万元，同比增长23%。"
        insert_response = client.post(
            "/api/v1/rag/insert",
            headers=auth_headers,
            json={
                "content": doc_content,
                "source": "financial_report"
            }
        )
        assert insert_response.status_code == 200
        assert insert_response.json()["status"] == "inserted"

        # ===== 第二步：向量检索 =====
        search_response = client.post(
            "/api/v1/rag/pg_search",
            headers=auth_headers,
            json={
                "question": "去年第四季度营收是多少？",
                "top_k": 3
            }
        )
        assert search_response.status_code == 200
        docs = search_response.json()["docs"]
        assert len(docs) > 0, "检索结果不应为空"

        # ===== 第三步：验证检索质量 =====
        # 检索到的第一条文档应该包含我们插入的内容
        retrieved_texts = [doc["content"] for doc in docs]
        found = any("5800万元" in text for text in retrieved_texts)
        assert found, f"检索结果中未找到'5800万元'，实际返回：{retrieved_texts}"

    def test_search_with_no_documents(self, client, auth_headers):
        """测试检索不相关的内容（数据库中无匹配文档的情况）"""
        response = client.post(
            "/api/v1/rag/pg_search",
            headers=auth_headers,
            json={
                "question": "火星上有没有外星人",
                "top_k": 3
            }
        )
        assert response.status_code == 200
        # 即使没找到相关文档，接口也应该正常返回
        docs = response.json()["docs"]
        assert isinstance(docs, list), "即使无结果，docs 也应该是空列表"

    def test_insert_and_immediate_search(self, client, auth_headers):
        """测试刚插入的文档能否立即被检索到"""
        # 插入一条独特信息
        unique_content = "测试集成专用：紫色长颈鹿在月球上跳芭蕾舞。"
        insert_resp = client.post(
            "/api/v1/rag/insert",
            headers=auth_headers,
            json={
                "content": unique_content,
                "source": "test"
            }
        )
        assert insert_resp.status_code == 200

        # 立即检索
        search_resp = client.post(
            "/api/v1/rag/pg_search",
            headers=auth_headers,
            json={
                "question": "紫色长颈鹿",
                "top_k": 3
            }
        )
        assert search_resp.status_code == 200
        docs = search_resp.json()["docs"]
        
        # 刚插入的文档应该能被检索到
        retrieved_contents = [doc["content"] for doc in docs]
        assert any("紫色长颈鹿" in content for content in retrieved_contents), \
            f"刚插入的文档未被检索到，实际返回：{retrieved_contents}"
        

    def test_insert_then_delete_then_search(self, client, auth_headers):
        """
        异常场景测试：
        1. 插入一条文档
        2. 立即删除它
        3. 再次检索，应找不到该文档内容
        """
        import uuid
        # 生成绝对唯一的标识符
        unique_id = str(uuid.uuid4())
        unique_content = f"测试删除专用-{unique_id}-紫色长颈鹿在月球上跳芭蕾舞。"
        # 插入
        insert_resp = client.post(
            "/api/v1/rag/insert",
            headers=auth_headers,
            json={"content":unique_content, "source":"test" }
        )
        assert insert_resp.status_code == 200

        # 从检索结果中找 ID（注意：pg_search 目前不返回 ID，所以改用数据库直查）
        from core.db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM documents WHERE content = %s",(unique_content,))
                row = cur.fetchone()
            assert row is not None, "刚插入的文档应该存在"
            doc_id = row[0]

            # 删除
            delete_resp = client.delete(
                f"/api/v1/rag/documents/{doc_id}",
                headers=auth_headers
            )
            assert delete_resp.status_code == 200

            import time
            # 等待数据库变更生效
            time.sleep(0.5)

            # 4. 用唯一ID精确检索，而不是依赖语义
            search_after = client.post(
                "/api/v1/rag/pg_search",
                headers=auth_headers,
                json={"question": unique_id, "top_k": 3}  # 用唯一ID作为查询词
            )
            docs_after = search_after.json()["docs"]
            retrieved_texts = [doc["content"] for doc in docs_after]
            # 断言：检索结果中不应包含这个唯一ID
            assert not any(unique_id in text for text in retrieved_texts), "已删除的文档不应该被检索到"