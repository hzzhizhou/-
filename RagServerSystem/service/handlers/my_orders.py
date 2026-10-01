"""确定性「我名下订单」列表查询：不带订单号时直接查库回传。

与带单号的订单查询分支互斥（本句有订单号就归那条精确查询）；
只在用户问"我有哪些订单"这类列表诉求时触发，问方法（怎么查订单）、
或句中是办理诉求（"我的订单有质量问题"）时都不拦 —— 判定规则见 shared.constants。
"""
from typing import AsyncIterator

from logs.log_config import log
from service.handlers.context import TurnContext
from shared.reply_templates import AGENT_NO_ANSWER_FALLBACK


async def handle_my_orders(ctx: TurnContext) -> AsyncIterator[str]:
    try:
        from agent.tools.order_query import list_my_orders as _lmo

        # 归属账号只能是登录账号，不接受会话/订单号兜底（否则可能列出他人订单）
        yield _lmo.invoke({"owner_id": ctx.user["user_id"]})
    except Exception as e:
        log.error(f"确定性名下订单查询失败: {e}", exc_info=True)
        yield AGENT_NO_ANSWER_FALLBACK