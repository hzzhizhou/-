# access/routes/user_orders.py
"""用户端「我的订单」只读接口。

权限：必须登录（require_user）。归属过滤直接写在 SQL 的 WHERE user_id 条件里
（infrastructure/mysql_store.list_orders_by_user），因此天然查不到他人订单，

"""
import asyncio

from fastapi import Depends

from access.routes.auth import require_user
from infrastructure import mysql_store


def register_user_order_routes(app) -> None:
    """把用户端订单只读接口挂到 FastAPI app。"""

    @app.get("/my/orders")
    async def my_orders(user: dict = Depends(require_user)):
        """当前登录用户名下的订单列表（最近下单在前，只读）。"""
        items, total = await asyncio.to_thread(
            mysql_store.list_orders_by_user, user["user_id"])
        return {"items": items, "total": total}
