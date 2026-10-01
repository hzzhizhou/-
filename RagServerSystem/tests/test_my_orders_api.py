"""用户端「我的订单」接口（GET /my/orders）测试。

锁住三件事：
  1) 必须登录：没带令牌直接 401，不返回任何订单；
  2) 只返回本人订单：接口把 user_id 传给查询层，看不到别人订单；
  3) 只读边界：不提供付款/取消/改状态的接口（改单要走工单流程）。
"""
import pytest  # noqa: F401  （pytest 注入 monkeypatch，导入以声明测试依赖）
from fastapi import FastAPI
from fastapi.testclient import TestClient

from access.routes import user_orders
from access.routes.auth import require_user
from infrastructure import mysql_store


def _client(user: dict | None) -> TestClient:
    """挂上被测路由；user 为 None 时走真实鉴权（用于 401 用例）。"""
    app = FastAPI()
    user_orders.register_user_order_routes(app)
    if user is not None:
        app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


def _order(oid: str, owner: str) -> dict:
    return {
        "order_id": oid, "user_id": owner, "customer": "陈*杰",
        "product": "iPhone 15", "amount": 4999.0, "status": "shipped",
        "logistics": "顺丰速运 SF2345678901", "tracking": "已揽收",
        "created_at": "2024-11-20 10:15:00",
    }


def test_anonymous_is_rejected(monkeypatch):
    # 无令牌 → 鉴权查不到用户 → 401，不能把订单吐给未登录访客
    monkeypatch.setattr(mysql_store, "get_user_by_token", lambda token: None)
    resp = _client(None).get("/my/orders")
    assert resp.status_code == 401
    assert "items" not in resp.json()


def test_only_own_orders_are_returned(monkeypatch):
    seen = {}

    def fake(uid, limit=20):
        seen["uid"] = uid
        return [_order("SO20241120005", uid)], 1

    monkeypatch.setattr(mysql_store, "list_orders_by_user", fake)
    resp = _client({"user_id": "owner1"}).get("/my/orders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [o["order_id"] for o in body["items"]] == ["SO20241120005"]
    assert all(o["user_id"] == "owner1" for o in body["items"])
    assert seen["uid"] == "owner1"        # 归属账号由登录态决定，不能被请求参数替换


def test_endpoint_is_read_only():
    """只有 GET：没有付款/取消/改状态入口，保证订单表不被用户端直接改写。"""
    app = FastAPI()
    user_orders.register_user_order_routes(app)
    methods = {
        method
        for route in app.routes if getattr(route, "path", "") == "/my/orders"
        for method in route.methods
    }
    assert methods == {"GET"}
