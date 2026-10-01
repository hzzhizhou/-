"""转人工相关接口测试。

两条主线：
  1. POST /handoff/request —— 转人工建单/复用会话；
  2. GET  /tickets/{id}/context —— 转人工前的机器人会话记录（客服接手上下文）。

背景：`ensure_handoff_session` 从接入层挪到 service/handoff_service.py 之后，
handoff.py 漏了这一行 import，接口一被调用就 NameError → 500，
前端只能看到一句笼统的「转人工失败，请稍后再试」，排查成本很高。

这里统一用 monkeypatch 去替换 handoff 模块命名空间里的名字：属性不存在时
monkeypatch 会直接报错，因此这些用例同时锁住「导入没漏」这件事。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import access.routes.handoff as handoff
from access.routes.auth import require_user
from infrastructure import mysql_store

_TICKET = {
    "ticket_id": "TK-TEST",
    "user_id": "u1",
    "status": "open",
    "created_at": "2026-01-01 00:00:00",
}


def _build_app(monkeypatch, ticket, context_msgs):
    """搭一个只挂 handoff 路由的 app，并把「查工单」和「查前置上下文」换成假数据。"""
    monkeypatch.setattr(mysql_store, "get_ticket", lambda tid: ticket)

    async def fake_context(uid, before_time):
        return context_msgs

    monkeypatch.setattr(handoff, "get_pre_handoff_messages", fake_context)
    app = FastAPI()
    handoff.register_handoff_routes(app)
    return app


def test_request_handoff_returns_ticket(monkeypatch):
    monkeypatch.setattr(
        handoff, "ensure_handoff_session",
        lambda uid, q="": ({"ticket_id": "TK-TEST", "status": "open",
                            "created_at": "2026-01-01 00:00:00"}, False),
    )
    app = FastAPI()
    handoff.register_handoff_routes(app)
    app.dependency_overrides[require_user] = lambda: {"user_id": "u1", "role": "user"}

    resp = TestClient(app).post("/handoff/request", json={"question": "转人工"})

    assert resp.status_code == 200
    assert resp.json()["ticket_id"] == "TK-TEST"


def test_context_returns_pre_handoff_messages(monkeypatch):
    """归属本人可读到自己转人工前与机器人的对话。"""
    msgs = [{"role": "user", "content": "我要退货", "timestamp": "2026-01-01 00:00:00"},
            {"role": "assistant", "content": "好的，请提供订单号", "timestamp": ""}]
    app = _build_app(monkeypatch, dict(_TICKET), msgs)
    app.dependency_overrides[require_user] = lambda: {"user_id": "u1", "role": "user"}

    resp = TestClient(app).get("/tickets/TK-TEST/context")

    assert resp.status_code == 200
    assert [m["content"] for m in resp.json()["messages"]] == ["我要退货", "好的，请提供订单号"]


def test_context_empty_when_no_prior_conversation(monkeypatch):
    """用户没聊过就直接转人工：返回空列表，而不是报错。"""
    app = _build_app(monkeypatch, dict(_TICKET), [])
    app.dependency_overrides[require_user] = lambda: {"user_id": "u1", "role": "user"}

    resp = TestClient(app).get("/tickets/TK-TEST/context")

    assert resp.status_code == 200
    assert resp.json()["messages"] == []


def test_context_denies_other_user_without_leaking(monkeypatch):
    """越权访问必须 403，且响应里不能出现任何上下文内容（不泄露他人对话）。"""
    app = _build_app(
        monkeypatch, dict(_TICKET),
        [{"role": "user", "content": "我的身份证号是 123", "timestamp": ""}],
    )
    app.dependency_overrides[require_user] = lambda: {"user_id": "u2", "role": "user"}

    resp = TestClient(app).get("/tickets/TK-TEST/context")

    assert resp.status_code == 403
    assert "身份证" not in resp.text


def test_context_allows_admin(monkeypatch):
    """客服（管理员）能读到客户转人工前的对话 —— 这正是本功能的目的。"""
    app = _build_app(
        monkeypatch, dict(_TICKET),
        [{"role": "user", "content": "物流到哪了", "timestamp": ""}],
    )
    app.dependency_overrides[require_user] = lambda: {"user_id": "admin1", "role": "admin"}

    resp = TestClient(app).get("/tickets/TK-TEST/context")

    assert resp.status_code == 200
    assert resp.json()["messages"][0]["content"] == "物流到哪了"


def test_context_404_when_ticket_missing(monkeypatch):
    app = _build_app(monkeypatch, None, [])
    app.dependency_overrides[require_user] = lambda: {"user_id": "u1", "role": "user"}

    resp = TestClient(app).get("/tickets/TK-NOT-EXIST/context")

    assert resp.status_code == 404


def test_latest_conversation_before_skips_query_on_blank_args():
    """参数不全时不查库直接返回 None（也保证无 MySQL 环境下这几条能被跑通）。"""
    assert mysql_store.get_latest_conversation_before("", "2026-01-01 00:00:00") is None
    assert mysql_store.get_latest_conversation_before("u1", "") is None
