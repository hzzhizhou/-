"""
账号体系 API：注册 / 登录 / 登出 / 当前用户 + 后台用户管理。

实现取舍（面试项目宁简勿繁）：
- 存储复用 infrastructure.mysql_store 的 users 表，不引入新数据层。
- 口令用标准库 hashlib.pbkdf2_hmac 加盐存储，不引入 bcrypt 等依赖。
- 会话用随机 token 存库（users.token），不引入 JWT 库；登出/禁用即作废。
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

from fastapi import Header, HTTPException
from pydantic import BaseModel

from infrastructure import mysql_store

_PBKDF2_ROUNDS = 100_000
_USERNAME_MIN, _USERNAME_MAX = 3, 20
_PASSWORD_MIN = 6

# 默认管理员：空库时自动创建，保证首次进入后台可登录
DEFAULT_ADMIN = ("admin", "admin123", "管理员")
# 演示买家：种子订单的归属账号，保证订单归属校验有真实归属数据可校验
DEFAULT_BUYER = ("customer", "customer123", "用户")


# ---------- 口令 ----------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ROUNDS
    )
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)
        )
        return secrets.compare_digest(got.hex(), digest)
    except (ValueError, AttributeError):
        return False


# ---------- 请求体 ----------

class RegisterReq(BaseModel):
    username: str
    password: str
    nickname: str | None = None


class LoginReq(BaseModel):
    username: str
    password: str


class UserStatusReq(BaseModel):
    status: str   # active | disabled


# ---------- 令牌 ----------

def _extract_token(authorization: str) -> str:
    """从 Authorization: Bearer <token> 中取令牌；缺失返回空串。"""
    if not authorization:
        return ""
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return authorization.strip()


def require_user(authorization: str = Header(default="")) -> dict:
    """取当前登录用户；无效/禁用则 401。"""
    user = mysql_store.get_user_by_token(_extract_token(authorization))
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已失效，请重新登录")
    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


def require_admin(authorization: str = Header(default="")) -> dict:
    """取当前登录用户并校验管理员角色；非管理员 403。

    后台管理接口（工单/订单/知识库/用户）统一挂这个依赖，
    普通用户即使已登录也无法读写后台数据。
    """
    user = require_user(authorization)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可访问后台管理接口")
    return user


def _validate(username: str, password: str) -> None:
    username = (username or "").strip()
    if not (_USERNAME_MIN <= len(username) <= _USERNAME_MAX):
        raise HTTPException(
            status_code=400,
            detail=f"用户名长度需在 {_USERNAME_MIN}-{_USERNAME_MAX} 个字符之间",
        )
    if len(password or "") < _PASSWORD_MIN:
        raise HTTPException(status_code=400, detail=f"密码至少 {_PASSWORD_MIN} 位")


def ensure_default_admin() -> None:
    """空库时创建默认管理员（幂等），便于首次演示直接登录后台。"""
    username, password, nickname = DEFAULT_ADMIN
    if mysql_store.get_user_by_username(username):
        return
    mysql_store.create_user(
        uuid.uuid4().hex, username, hash_password(password), nickname, role="admin"
    )
    import logging
    logging.getLogger("auth").info(f"已创建默认管理员：{username} / {password}")


def ensure_default_buyer() -> None:
    """演示买家账号（幂等）：种子订单挂它名下，归属校验才有真实数据可校验。

    不建这个账号的话，种子订单没有归属账号，任何登录账号都能查到别人的订单；
    建了之后「用别的账号查同一张订单」会被拒，正好演示归属校验生效。
    """
    import logging
    logger = logging.getLogger("auth")
    username, password, nickname = DEFAULT_BUYER
    user = mysql_store.get_user_by_username(username)
    if not user:
        user = mysql_store.create_user(
            uuid.uuid4().hex, username, hash_password(password), nickname, role="user"
        )
        if user:
            logger.info(f"已创建演示买家：{username} / {password}")
    if not user:
        return
    bound = mysql_store.bind_ownerless_orders(user["user_id"])
    if bound:
        logger.info(f"已把 {bound} 张无归属订单绑定到演示买家 {username}")


def register_auth_routes(app) -> None:
    """把账号相关接口挂到 FastAPI app。"""
    try:
        ensure_default_admin()
        ensure_default_buyer()
    except Exception as e:  # 种子失败不影响服务启动
        import logging
        logging.getLogger("auth").error(f"默认账号创建失败: {e}")

    # ---------- 注册 / 登录 ----------
    @app.post("/auth/register")
    async def register(req: RegisterReq):
        _validate(req.username, req.password)
        username = req.username.strip()
        user = mysql_store.create_user(
            uuid.uuid4().hex, username, hash_password(req.password),
            (req.nickname or "").strip() or username,
        )
        if user is None:
            raise HTTPException(status_code=409, detail=f"用户名已存在: {username}")
        # 注册即登录，直接下发令牌，前端无需再跳一次登录
        token = secrets.token_urlsafe(32)
        mysql_store.update_user_login(user["user_id"], token)
        return {"token": token, "user": mysql_store.get_user_by_id(user["user_id"])}

    @app.post("/auth/login")
    async def login(req: LoginReq):
        row = mysql_store.get_user_by_username((req.username or "").strip())
        if not row or not verify_password(req.password, row.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if row.get("status") != "active":
            raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")
        token = secrets.token_urlsafe(32)
        mysql_store.update_user_login(row["user_id"], token)
        return {"token": token, "user": mysql_store.get_user_by_id(row["user_id"])}

    @app.post("/auth/logout")
    async def logout(authorization: str = Header(default="")):
        user = mysql_store.get_user_by_token(_extract_token(authorization))
        if user:
            mysql_store.clear_user_token(user["user_id"])
        return {"ok": True}

    @app.get("/auth/me")
    async def me(authorization: str = Header(default="")):
        return {"user": require_user(authorization)}

    # ---------- 用户管理（后台，仅管理员） ----------
    @app.get("/users")
    async def list_users(keyword: str = "", page: int = 1, page_size: int = 10,
                         authorization: str = Header(default="")):
        require_admin(authorization)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        items, total = mysql_store.list_users(page, page_size, keyword.strip())
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @app.patch("/users/{user_id}")
    async def update_user(user_id: str, req: UserStatusReq,
                          authorization: str = Header(default="")):
        current = require_admin(authorization)
        if req.status not in ("active", "disabled"):
            raise HTTPException(status_code=400, detail="非法状态，可选：active, disabled")
        if user_id == current["user_id"] and req.status == "disabled":
            raise HTTPException(status_code=400, detail="不能禁用当前登录账号")
        row = mysql_store.update_user_status(user_id, req.status)
        if row is None:
            raise HTTPException(status_code=404, detail=f"用户不存在: {user_id}")
        return row

    @app.delete("/users/{user_id}")
    async def delete_user(user_id: str, authorization: str = Header(default="")):
        current = require_admin(authorization)
        if user_id == current["user_id"]:
            raise HTTPException(status_code=400, detail="不能删除当前登录账号")
        if not mysql_store.delete_user(user_id):
            raise HTTPException(status_code=404, detail=f"用户不存在: {user_id}")
        return {"deleted": user_id}