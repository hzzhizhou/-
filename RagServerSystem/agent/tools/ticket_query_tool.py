"""
智能客服 - 售后工单处理进度查询工具
查询用户已提交工单的当前处理环节与人工客服备注，实现"答复回传"闭环：
- 客服机器人自动建单 → 人工客服在管理端推进业务环节并填写备注(note)
- 用户再次询问"我的退货办得怎么样"时，机器人按环节拼装完整答复

回答完整度标准（对齐企业客服话术四要素）：
当前环节 + 已完成哪些环节 + 下一步谁做什么 + 时效承诺 + 联系方式。
"""
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from langchain_core.tools import tool

from logs.log_config import log
from infrastructure.mysql_store import query_ticket_progress
from shared.reply_templates import (
    NEED_ORDER_NO,
    NO_TICKET,
    RETURN_PROGRESS,
    RETURN_PROGRESS_ABNORMAL,
    RETURN_PROGRESS_NOTE,
    RETURN_STAGE_EXCEPTIONS,
    SERVICE_CONTACT,
    TICKET_PROGRESS_NO_NOTE,
    TICKET_PROGRESS_WITH_NOTE,
    category_cn,
    render_stage_timeline,
    stage_cn,
    stage_def,
    stage_step,
    status_cn,
)

# 走阶段化流转的售后分类
_STAGED_CATEGORY = {"return", "refund"}


def _render_staged_progress(ticket: dict, subject: str) -> str:
    """退货/退款工单：按业务环节拼装完整进度答复（含流转轨迹、下一步、时效、联系方式）。"""
    stage = (ticket.get("stage") or "").strip()
    # 分类决定轨迹长度：纯退款不退货，轨迹里不出现「寄回商品 / 商家验收」
    category = (ticket.get("category") or "").strip()
    note = (ticket.get("note") or "").strip()
    note_line = RETURN_PROGRESS_NOTE.format(note=note) if note else ""
    updated_at = ticket.get("stage_updated_at") or ticket.get("created_at") or ""
    d = stage_def(stage)
    common = dict(
        subject=subject,
        ticket_id=ticket["ticket_id"],
        order_id=ticket.get("order_id") or "未关联订单",
        stage_cn=d["cn"],
        updated_at=updated_at,
        next=d["next"],
        note_line=note_line,
        contact=SERVICE_CONTACT,
    )

    # 非顺序环节（如审核未通过）：流程已终止，不再展示进度轨迹
    if stage in RETURN_STAGE_EXCEPTIONS:
        return RETURN_PROGRESS_ABNORMAL.format(**common)

    step, total = stage_step(stage, category)
    return RETURN_PROGRESS.format(
        step=step,
        total=total,
        sla=d["sla"],
        timeline=render_stage_timeline(ticket.get("stage_history"), stage, category),
        **common,
    )


@tool(description="根据订单号或用户标识查询该用户最近一张工单的处理进度：退货/退款类工单会返回当前业务环节、流转轨迹、下一步与时效；其他工单返回处理状态与人工客服备注。当用户询问'退货办到哪一步了/工单处理得怎么样/投诉有结果吗/转人工后续怎么处理'时调用。owner_id 传「当前用户账号标识」（系统提示中给出），用于把查询限定在本账号名下；不传时会退化为按订单号全库匹配，可能查到他人工单。")
def query_ticket(user_id: str = "", owner_id: str = "") -> str:
    """
    智能客服工单进度查询工具：
    - 输入：user_id 订单号（如 SO20241120005）或「当前用户账号标识」（系统提示中给出）
    - 输入：owner_id 当前用户账号标识，用于限定工单归属（种子订单号被多账号共用，
      不加限定会出现「查到别人工单」）
    - 输出：最近一张工单的处理进度（阶段化售后为完整环节说明 + 人工客服答复回传）
    """
    query = (user_id or "").strip()
    if not query:
        return NEED_ORDER_NO
    # 归属账号只认账号标识（32 位十六进制），防止模型误填订单号后把查询收窄成空结果
    _owner = (owner_id or "").strip()
    owner = _owner if re.fullmatch(r"[0-9a-f]{32}", _owner) else ""
    log.info(f"客服工具 query_ticket 被调用，查询依据：{query}，归属限定：{owner or '无'}")

    ticket = query_ticket_progress(query, owner=owner)
    if not ticket:
        return NO_TICKET

    subject = "退货" if ticket.get("category") == "return" else "退款"
    # 退货/退款且已进入业务环节：给完整流程答复（用户能看懂"现在到哪、还要做什么"）
    if ticket.get("category") in _STAGED_CATEGORY and (ticket.get("stage") or "").strip():
        return _render_staged_progress(ticket, subject)

    note = (ticket.get("note") or "").strip()
    template = TICKET_PROGRESS_WITH_NOTE if note else TICKET_PROGRESS_NO_NOTE
    return template.format(
        subject=category_cn(ticket.get("category")),
        ticket_id=ticket["ticket_id"],
        status=status_cn(ticket.get("status")) or stage_cn(ticket.get("stage")),
        created_at=ticket.get("created_at", ""),
        note=note,
        contact=SERVICE_CONTACT,
    )


if __name__ == "__main__":
    print("--- 查询退货工单（按订单号）---")
    print(query_ticket.invoke({"user_id": "SO20241120005"}))
    print("--- 空参数 ---")
    print(query_ticket.invoke({"user_id": ""}))