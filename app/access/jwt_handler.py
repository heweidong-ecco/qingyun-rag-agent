import jwt
#    原来只有 C3 删掉的那两行 `os.getenv(...)` 在用它。属 §三·D1（未使用导入）范畴，顺手清掉。
from datetime import datetime, timedelta
from typing import Optional
from core.config import JWT_SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS

# 密钥（生产环境应从环境变量读取，且要足够复杂）
SECRET_KEY = JWT_SECRET_KEY
ALGORITHM = "HS256"

# Token 有效期
#    把上面 `from config import` 进来的同名常量**覆盖**了 ⇒ 那个 import 是**死导入**。
#    已核实**两边默认值相同**（`config.py:32-33` 也是 15 / 7）、且读的是**同一个 env 变量**
#    ⇒ **行为完全等价**，所以直接删掉这两行、**让 `config.py` 成为唯一来源**



def create_access_token(user_name: str) -> str:
    """生成 access_token，有效期15分钟"""
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_name,
        "type": "access",
        "exp": expire,
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_name: str) -> str:
    """生成 refresh_token，有效期7天"""
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_name,
        "type": "refresh",
        "exp": expire,
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """解码并验证Token，成功返回payload，失败返回None"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None  # Token已过期
    except jwt.InvalidTokenError:
        return None  # Token无效


def verify_access_token(token: str) -> Optional[str]:
    """验证access_token，返回user_name或None"""
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "access":
        return None  # 类型不对，可能是拿refresh_token来调接口
    return payload.get("sub")


def verify_refresh_token(token: str) -> Optional[str]:
    """验证refresh_token，返回user_name或None"""
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "refresh":
        return None  # 类型不对
    return payload.get("sub")