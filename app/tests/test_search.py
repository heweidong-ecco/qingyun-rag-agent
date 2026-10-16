import pytest

# 整篇标记 `needs_db` —— 走真实 pgvector 检索，**不进 CI**（CI 无 postgres service）。
# 本机跑：`cd app && POSTGRES_DB=rag_test pytest app/ -m "not integration"`。
pytestmark = pytest.mark.needs_db


def test_pg_search_empty(client, auth_headers):
    response = client.post("/api/v1/rag/pg_search",
        headers=auth_headers,
        json={"question": "Python编程", "top_k": 3}
    )
    assert response.status_code == 200

def test_pg_search_with_data(client, auth_headers, sample_document):
    # 先插入一条
    client.post("/api/v1/rag/insert", headers=auth_headers, json=sample_document)
    response = client.post("/api/v1/rag/pg_search",
        headers=auth_headers,
        json={"question": "Python", "top_k": 3}
    )
    assert response.status_code == 200