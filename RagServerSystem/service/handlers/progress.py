"""确定性工单进度查询：命中关键词直接读工单（含人工备注）回传，绕过 ReAct。

背景：纯提示词难以约束 LLM 在"订单号 + 工单/售后"场景优先选 query_ticket，
模型常被 order_id 拽回 query_order。改由规则确定性判定，保证"答复回传"闭环必达。
"""
import re
from typing import AsyncIterator

from logs.log_config import log
from service.handlers.context import TurnContext
from shared.reply_templates import PROGRESS_FALLBACK


async def handle_ticket_progress(ctx: TurnContext) -> AsyncIterator[str]:
    try:
        from agent.tools.ticket_query_tool import query_ticket as _qt

        # 查询依据优先级：本句订单号 > 会话已收集的订单号 > 登录账号
        # （用户追问"退货进度"时常不带单号，靠 DST 槽位里的订单号兜底；
        #   槽位也没有时按账号查，保证换会话后仍能查到自己以前的工单）
        m = re.search(r"SO\d+", ctx.question)
        if m:
            uid = m.group(0)
        elif ctx.dst is not None and ctx.dst.slots.get("order_id"):
            uid = ctx.dst.slots["order_id"]
        else:
            uid = ctx.user["user_id"]
        yield _qt.invoke({"user_id": uid, "owner_id": ctx.user["user_id"]})
    except Exception as e:
        log.error(f"确定性工单进度查询失败: {e}", exc_info=True)
        yield PROGRESS_FALLBACK