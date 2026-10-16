

import pytest


# test_main.py
def test_service_index(client):
    """**服务索引 JSON** 仍在 —— 它 从 `GET /` **挪到了 `GET /api/v1/info`**。

    **这条用例的形状变了，但不是"改断言让它过"**：`GET /` 当天按裁定改成了
    **总览首页**（302 → `/static/web/index.html`），而那段 JSON 的裁定原话是
    「**挪走，不是删**」⇒ 内容**一个字没改**，只是换了地址。
    `/` 本身的新契约由 `app/tests/test_index_page.py` 守着（别在这条里重复钉它）。
    """
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert set(response.json()["services"]) == {"public", "rag", "agent"}

@pytest.mark.needs_db
def test_health(client):
    """测试健康检查

    **单条标记 `needs_db`**（本文件其余两条不需要）——
    `/health` 会真去探 Postgres 与 Redis 的连通性，没有库就返回 **503**。
    ⇒ 不进 CI；本机跑时带 `POSTGRES_DB=rag_test`。
    """
    response = client.get("/health")
    assert response.status_code == 200

def test_docs_accessible(client):
    """测试 Swagger 文档可访问"""
    response = client.get("/docs")
    assert response.status_code == 200