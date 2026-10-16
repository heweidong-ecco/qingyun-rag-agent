import pytest

# **整篇标记 `needs_db`** —— 这些用例会**真往 `documents` 表写**，而且**没有 cleanup**，
# 而那张表是 `agent-eval-gate` 评测用的知识库（历史上已被污染 29 行）。
# ⇒ **不进 CI**（CI 只有 redis service，没有 postgres）；本机跑时**必须**带库名隔离：
#       cd app && POSTGRES_DB=rag_test pytest app/ -m "not integration"
pytestmark = pytest.mark.needs_db


def test_insert_document(client, auth_headers, sample_document):
    response = client.post("/api/v1/rag/insert",
        headers=auth_headers,
        json=sample_document
    )
    assert response.status_code == 200

def test_insert_batch(client, auth_headers):
    docs = {
        "documents": [
            {"content": "测试文档一", "source": "test"},
            {"content": "测试文档二", "source": "test"},
            {"content": "测试文档三", "source": "test"},
        ]
    }
    response = client.post("/api/v1/rag/insert_batch",
        headers=auth_headers,
        json=docs
    )
    assert response.status_code == 200

def test_delete_document_not_found(client, auth_headers):
    response = client.delete("/api/v1/rag/documents/99999",
        headers=auth_headers
    )
    assert response.status_code == 404