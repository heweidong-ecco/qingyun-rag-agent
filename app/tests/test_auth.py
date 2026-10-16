# test_auth.py
from core.config import LOGIN_USER_NAME, LOGIN_PASSWORD


def _login(client, password=None):
    """统一的登录调用。

    口令从 `config` 读（= `.env` 的 `LOGIN_PASSWORD`），**不硬编码** ——
    。
    """
    return client.post("/api/v1/auth/login", json={
        "user_name": LOGIN_USER_NAME,
        "password": LOGIN_PASSWORD if password is None else password,
    })


def test_login_success(client):
    """测试正常登录"""
    assert LOGIN_PASSWORD, "需在 .env 设置 LOGIN_PASSWORD（应用启动也会因此失败）"
    response = _login(client)
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"

def test_login_failure(client):
    """测试密码错误

    刻意构造一个**保证不等于**真实口令的错值（而不是写字面量），
    既能防止"口令恰好被设成那个字面量"的假失败，也顺带守住最要紧的回归：
    **口令来源换成环境变量后，错口令仍必须是 401，不能变成 500。**
    """
    response = _login(client, password=(LOGIN_PASSWORD or "") + "_definitely_wrong")
    assert response.status_code == 401

def test_missing_api_key(client):
    """测试不带API Key访问受保护接口"""
    response = client.post("/api/v1/rag/pg_search", json={
        "question": "test",
        "top_k": 3
    })
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_MISSING"

def test_refresh_token(client):
    """测试Token刷新流程"""
    # 先登录
    login_resp = _login(client)
    assert login_resp.status_code == 200, "登录失败 —— 先看到真因，别让 KeyError 掩盖它"
    refresh_token = login_resp.json()["refresh_token"]

    # 用refresh_token获取新access_token
    response = client.post("/api/v1/auth/refresh", json={
        "refresh_token": refresh_token
    })
    assert response.status_code == 200
    assert "access_token" in response.json()

def test_refresh_with_wrong_type(client):
    """测试用access_token去刷新（应该失败）"""
    login_resp = _login(client)
    assert login_resp.status_code == 200, "登录失败 —— 先看到真因，别让 KeyError 掩盖它"
    access_token = login_resp.json()["access_token"]

    response = client.post("/api/v1/auth/refresh", json={
        "refresh_token": access_token  # 故意传错类型
    })
    assert response.status_code == 401
