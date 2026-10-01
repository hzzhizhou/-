# access/routes/handoff.py
"""
转人工实时对话（人工客服接管）

两条约定：

1. 人工会话的判据是「该工单存在 ticket_messages 记录」。
   - 用户主动转人工（POST /handoff/request）
   - AI 判定投诉后确定性建单（service/handlers/complaint.py 的投诉分支调用
     service.handoff_service.start_handoff_session）
   退货/退款工单走阶段化流转（stage 联动订单状态），不写消息，因此不会被误认成人工会话。
   会话生命周期（建单/复用/写开场白）属业务，落在 service/handoff_service.py；
   本模块只保留 HTTP/WS 传输与连接管理。

2. WebSocket 只做下行推送，发送一律走 HTTP POST /tickets/{id}/messages。
   这样：令牌走 Authorization header（复用 require_user）；发送失败有正常 4xx 反馈；
   前端复用现成的 axios 实例；WS 侧逻辑只有「有新消息就推给你」，不易出错。
"""
import asyncio
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from logs.log_config import log
from infrastructure import mysql_store
from access.routes.auth import require_admin, require_user
from service.handoff_service import ensure_handoff_session
from access.history import get_pre_handoff_messages
from shared.reply_templates import HANDOFF_CLOSED

_CLOSED = "closed"


# ---------- 连接管理 ----------

class ConnectionManager:
    """ticket_id → 该工单当前的 WebSocket 连接集合（用户端 + 客服端）。

    单进程 uvicorn 下用内存字典足够；若将来多 worker 部署，
    需换成 Redis pub/sub 才能跨进程广播。
    """

    def __init__(self) -> None:
        self._rooms: Dict[str, Set[WebSocket]] = {}

    def connect(self, ticket_id: str, ws: WebSocket) -> None:
        """登记一个已 accept 的连接。"""
        self._rooms.setdefault(ticket_id, set()).add(ws)

    def disconnect(self, ticket_id: str, ws: WebSocket) -> None:
        room = self._rooms.get(ticket_id)
        if not room:
            return
        room.discard(ws)
        if not room:
            self._rooms.pop(ticket_id, None)

    async def broadcast(self, ticket_id: str, payload: dict) -> None:
        """推给该工单的所有连接；发送失败的连接直接剔除，避免死连接堆积。"""
        for ws in list(self._rooms.get(ticket_id, ())):
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(ticket_id, ws)


manager = ConnectionManager()


# ---------- 路由 ----------

class MessageReq(BaseModel):
    content: str


class HandoffReq(BaseModel):
    question: str = ""


def _is_participant(ticket: dict, user: dict) -> bool:
    """工单归属者本人或管理员可参与该会话。"""
    return user.get("role") == "admin" or ticket.get("user_id") == user.get("user_id")


def register_handoff_routes(app) -> None:
    """把人工会话相关接口挂到 FastAPI app。"""

    # ---------- WebSocket：只做下行推送 ----------
    @app.websocket("/ws/handoff/{ticket_id}")
    async def handoff_socket(ws: WebSocket, ticket_id: str, token: str = Query(default="")):
        """人工会话下行通道。

        浏览器 WebSocket 构造器不支持自定义 header，令牌只能走 query string。
        令牌会进入 access log，生产环境应改为一次性短期 ticket。
        """
        # 先 accept 再校验：未 accept 就 close，Starlette 只会回一个 HTTP 403，
        # 前端拿不到可区分的 WebSocket 关闭码（4001/4003）。
        await ws.accept()
        user = await asyncio.to_thread(mysql_store.get_user_by_token, token)
        if not user or user.get("status") != "active":
            await ws.close(code=4001)
            return
        ticket = await asyncio.to_thread(mysql_store.get_ticket, ticket_id)
        if not ticket or not _is_participant(ticket, user):
            await ws.close(code=4003)
            return

        manager.connect(ticket_id, ws)
        try:
            # 只读循环：消息一律由 HTTP POST 发送，这里仅维持连接，内容丢弃
            while True:
                msg = await ws.receive()
                # 原始 receive() 收到断开消息时返回 dict 而不抛异常；不在这里退出，
                # 下一次 receive() 会抛 RuntimeError（每次断开都刷一条无意义的警告）
                if msg.get("type") == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.warning(f"人工会话 WS 异常：{e}")
        finally:
            manager.disconnect(ticket_id, ws)

    # ---------- HTTP ----------
    @app.post("/handoff/request")
    async def request_handoff(req: HandoffReq, user: dict = Depends(require_user)):
        """用户主动转人工：已有进行中会话则复用，否则新建工单并开启会话。"""
        ticket, reused = await asyncio.to_thread(
            ensure_handoff_session, user["user_id"], req.question)
        return {
            "ticket_id": ticket["ticket_id"],
            "reused": reused,
            "status": ticket["status"],
            "created_at": ticket["created_at"],
        }

    @app.get("/handoff/active")
    async def active_handoff(user: dict = Depends(require_user)):
        """当前用户进行中的人工会话工单；没有则 ticket 为 null。"""
        ticket = await asyncio.to_thread(mysql_store.get_handoff_ticket, user["user_id"])
        return {"ticket": ticket}

    @app.get("/handoff/sessions", dependencies=[Depends(require_admin)])
    async def handoff_sessions():
        """客服工作台：会话列表（带最后一条消息，按最新消息倒序）。"""
        return {"sessions": await asyncio.to_thread(mysql_store.list_handoff_sessions)}

    @app.get("/tickets/{ticket_id}/messages")
    async def get_messages(ticket_id: str, after_id: int = 0,
                           user: dict = Depends(require_user)):
        """会话历史；after_id 用于断线重连时增量补消息。"""
        ticket = await asyncio.to_thread(mysql_store.get_ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail=f"工单不存在: {ticket_id}")
        if not _is_participant(ticket, user):
            raise HTTPException(status_code=403, detail="无权访问该会话")
        msgs = await asyncio.to_thread(
            mysql_store.list_ticket_messages, ticket_id, after_id)
        return {"ticket_id": ticket_id, "messages": msgs, "status": ticket["status"]}

    @app.get("/tickets/{ticket_id}/context")
    async def get_pre_handoff_context(ticket_id: str, user: dict = Depends(require_user)):
        """转人工前的机器人会话记录：客服接手时不用再问「您好，请问有什么可以帮您」。

        与 /messages 分开是为了保持各自职责单一：消息接口会被 WebSocket 断线重连反复调用，
        而这段上下文只在打开会话时取一次，不必每次重连都拉一遍历史。
        权限与消息接口一致（归属者本人或管理员），越权时 403 且不返回任何内容。
        """
        ticket = await asyncio.to_thread(mysql_store.get_ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail=f"工单不存在: {ticket_id}")
        if not _is_participant(ticket, user):
            raise HTTPException(status_code=403, detail="无权访问该会话")
        msgs = await get_pre_handoff_messages(
            ticket.get("user_id") or "", ticket.get("created_at") or "")
        return {"ticket_id": ticket_id, "messages": msgs}

    @app.post("/tickets/{ticket_id}/messages")
    async def post_message(ticket_id: str, req: MessageReq,
                           user: dict = Depends(require_user)):
        """发送消息（用户端与客服端共用）：写库后经 WebSocket 推给该工单所有连接。"""
        content = (req.content or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="消息内容不能为空")
        ticket = await asyncio.to_thread(mysql_store.get_ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail=f"工单不存在: {ticket_id}")
        if not _is_participant(ticket, user):
            raise HTTPException(status_code=403, detail="无权在该会话发言")
        if ticket["status"] == _CLOSED:
            raise HTTPException(status_code=400, detail="该会话已结束")

        sender = "agent" if user.get("role") == "admin" else "user"
        msg = await asyncio.to_thread(
            mysql_store.add_ticket_message, ticket_id, sender, content,
            user["user_id"], user.get("nickname") or user.get("username") or "",
        )
        await manager.broadcast(ticket_id, {"type": "message", "message": msg})
        return msg

    @app.post("/handoff/{ticket_id}/close")
    async def close_handoff(ticket_id: str, _: dict = Depends(require_admin)):
        """人工客服结束会话：工单置为已关闭，并推系统提示让用户端退回智能客服。"""
        ticket = await asyncio.to_thread(mysql_store.get_ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail=f"工单不存在: {ticket_id}")
        await asyncio.to_thread(
            mysql_store.update_ticket_status, ticket_id, status=_CLOSED)
        msg = await asyncio.to_thread(
            mysql_store.add_ticket_message, ticket_id, "system", HANDOFF_CLOSED)
        await manager.broadcast(ticket_id, {"type": "session_closed", "message": msg})
        log.info(f"人工会话已结束：{ticket_id}")
        return {"status": "success", "ticket_id": ticket_id}