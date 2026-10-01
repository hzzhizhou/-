"""确定性订单查询：问题里带订单号且意图为订单/物流，直接查库回传。

背景：模型偶发不吐结构化 tool_calls，而是把 ReAct 轨迹（含它自己编造的
Observation）当正文输出，被剥离后判定「未产出有效正文」转知识兜底，
用户看到「没查到可靠的资料」。带单号的查询本身是可确定性完成的动作，
改由规则直接查库：既保证必达，也保证数据真实（不经模型转述、不会被编造）。
"""
import re
from typing import AsyncIterator

from logs.log_config import log
from service.handlers.context import TurnContext
from shared.reply_templates import AGENT_NO_ANSWER_FALLBACK, ORDER_FORBIDDEN, ORDER_TAIL


async def handle_order_query(ctx: TurnContext) -> AsyncIterator[str]:
    """调用前提（由调度方判定）：本句含订单号，且意图为 order/logistics。"""
    try:
        from agent.tools.order_query import query_order as _qo

        m = re.search(r"SO\d+", ctx.question)
        # 带上归属账号：非本账号订单会被归属校验拦下（不返回任何订单字段）
        resp = _qo.invoke({"order_id": m.group(0), "owner_id": ctx.user["user_id"]})
        # 被归属校验拒绝时不再追加拿单建议，避免答复自相矛盾
        yield resp if ORDER_FORBIDDEN in resp else f"{resp}\n\n{ORDER_TAIL}"
    except Exception as e:
        log.error(f"确定性订单查询失败: {e}", exc_info=True)
        yield AGENT_NO_ANSWER_FALLBACK