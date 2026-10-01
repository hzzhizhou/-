"""DST 槽位 Schema 单元测试：必填槽位、槽位格式正则、提示文本。"""
from agent.dst.slot_schema import (
    Stage, required_slots, all_slots, slot_prompt_desc, ORDER_ID_RE, AMOUNT_RE,
)


# ---------- required_slots / all_slots ----------
def test_required_slots_return():
    assert required_slots("return") == {"order_id", "reason"}


def test_required_slots_varying():
    assert required_slots("refund") == {"order_id"}
    assert required_slots("logistics") == {"order_id"}
    assert required_slots("complaint") == {"reason"}


def test_no_business_slots_for_light_intents():
    assert required_slots("consult") == set()
    assert required_slots("chat") == set()
    assert required_slots("unknown_intent") == set()


def test_return_optional_slots_are_extra():
    assert {"product", "amount"} <= all_slots("return")


def test_slot_prompt_desc_empty_for_no_slots():
    assert slot_prompt_desc("chat") == ""


def test_slot_prompt_desc_marks_required():
    desc = slot_prompt_desc("return")
    assert "order_id" in desc
    assert "必填" in desc


# ---------- 槽位格式正则 ----------
def test_order_id_re_matches():
    assert ORDER_ID_RE.search("我的订单 SO20241120005 在哪") is not None
    assert ORDER_ID_RE.search("查询 ABC1234567") is not None


def test_amount_re_requires_unit():
    # 正确：带 ¥ 前缀或 元 后缀
    assert AMOUNT_RE.search("退款 ¥199") is not None
    assert AMOUNT_RE.search("应退 199.5元") is not None
    # 错误：纯数字（如订单号）不带符号 → 不得误判为金额
    assert AMOUNT_RE.search("订单 20241120005") is None


# ---------- Stage 枚举 ----------
def test_stage_enum_values():
    assert Stage.COLLECTING == "COLLECTING"
    assert Stage.CONFIRMING == "CONFIRMING"
    assert Stage.EXECUTING == "EXECUTING"
    assert Stage.DONE == "DONE"