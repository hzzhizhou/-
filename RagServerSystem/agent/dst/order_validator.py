"""
订单槽位验证器（Entity/Value Resolver）

企业 DST 中，槽位一填充后会交由独立的"验证层"去后端(OMS/ERP)校验，校验结果再回流给 NLG。
本模块把"退货订单槽位"的存在性与状态可退性统一封装成一个验证器：
  - 输入：order_id
  - 输出：结构化结果 { valid, status, status_raw, message }
使其与 DST 状态机解耦——状态机只根据 valid 决定"确认或回收集"，不直接查库。
"""
from typing import Optional, Dict, Any

# 允许办理退货/退款的订单销售状态
RETURNABLE_STATUS = {"paid", "shipped", "delivered"}

# 非可退状态的用户提示文案
_STATUS_NOT_RETURNABLE_HINT: Dict[str, str] = {
    "pending": "订单尚未付款，无需办理退货，可直接取消下单",
    "cancelled": "订单已取消，无法办理退货",
    "refunding": "订单退款处理中，暂不支持重复申请退货",
}


def validate_return_order(order_id: Optional[str],
                          owner_id: str = "") -> Dict[str, Any]:
    """
    校验退货/退款订单槽位是否合法可用。
    返回结构化结果：
      { valid, status, order_id, status_raw, message }
    - status: 'not_found' | 'forbidden' | 'not_returnable' | 'ok'
    - owner_id: 当前登录账号标识，订单归属校验用；非本账号订单不予办理，
      否则别人只要拿到订单号就能替你发起退货（商品、金额、收货信息全在里面）。
    """
    order_id = (order_id or "").strip()

    if not order_id:
        return {
            "valid": False, "status": "not_found", "order_id": "",
            "status_raw": None,
            "message": "请提供订单号，以便为您办理退货/退款。",
        }

    from infrastructure.mysql_store import can_access_order, get_order
    order = get_order(order_id)

    if order is None:
        return {
            "valid": False, "status": "not_found", "order_id": order_id,
            "status_raw": None,
            "message": (f"很抱歉，未查询到订单号 **{order_id}** 的订单，"
                        f"请核对后重新提供正确的订单号。"),
        }

    # 归属校验：非本账号订单不进入退货/退款流程（提示与查询侧口径一致）
    if not can_access_order(order, owner_id):
        return {
            "valid": False, "status": "forbidden", "order_id": order_id,
            "status_raw": order.get("status"),
            "message": (f"订单 **{order_id}** 不在当前登录账号名下，无法为您办理退货/退款。\n"
                        f"请用下单时使用的账号登录后再办理；"
                        f"如确认订单归属无误，回复“转人工”，我为您转接人工客服核实。"),
        }

    raw = order.get("status")
    if raw not in RETURNABLE_STATUS:
        hint = _STATUS_NOT_RETURNABLE_HINT.get(raw, "暂不支持办理退货")
        return {
            "valid": False, "status": "not_returnable", "order_id": order_id,
            "status_raw": raw,
            "message": (f"很抱歉，订单 **{order_id}** 当前状态为 **{raw}**（{hint}）。"
                        f"可更换其他订单号重试，或转接人工客服协助。"),
        }

    return {
        "valid": True, "status": "ok", "order_id": order_id,
        "status_raw": raw, "message": "",
    }
