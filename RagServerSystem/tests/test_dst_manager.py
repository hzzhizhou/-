"""DST 会话状态管理单元测试：槽位合并、阶段推进、会话记忆提示（纯逻辑，不触库）。"""
from agent.dst.dst_manager import ConversationState, DSTManager
from agent.dst.slot_schema import Stage


def _state(intent="", slots=None, stage=None):
    return ConversationState(
        session_id="test",
        intent=intent,
        slots=dict(slots or {}),
        stage=stage or Stage.COLLECTING,
    )


# ---------- merge_new_slots ----------
def test_merge_new_slots_accumulates():
    s = _state(intent="return", slots={"order_id": "SO20241120005"})
    out = DSTManager.merge_new_slots(s, {"reason": "商品破损"})
    assert out.slots == {"order_id": "SO20241120005", "reason": "商品破损"}
    # 原地更新同一对象
    assert s.slots == out.slots


def test_merge_new_slots_overrides_existing():
    s = _state(intent="return", slots={"reason": "尺码不合适"})
    out = DSTManager.merge_new_slots(s, {"reason": "商品破损"})
    assert out.slots["reason"] == "商品破损"


# ---------- advance_stage ----------
def test_stage_stays_collecting_when_missing_required():
    s = _state(intent="return", slots={})   # 缺 order_id / reason
    assert DSTManager.advance_stage(s, tool_executed=False) == Stage.COLLECTING


def test_stage_executing_when_slots_complete_and_tool_used():
    s = _state(intent="return", slots={"order_id": "SO1234567", "reason": "破损"})
    assert DSTManager.advance_stage(s, tool_executed=True) == Stage.EXECUTING


def test_stage_done_when_slots_complete_and_answer_given():
    s = _state(intent="return", slots={"order_id": "SO1234567", "reason": "破损"}, )
    s.answer = "已登记"
    assert DSTManager.advance_stage(s, tool_executed=False) == Stage.DONE


def test_stage_confirming_when_slots_complete_no_answer():
    s = _state(intent="return", slots={"order_id": "SO1234567", "reason": "破损"})
    assert DSTManager.advance_stage(s, tool_executed=False) == Stage.CONFIRMING


# ---------- build_system_context ----------
def test_context_empty_when_no_state():
    assert DSTManager.build_system_context(_state()) == ""


def test_context_shows_slots_and_missing_when_collecting():
    ctx = DSTManager.build_system_context(
        _state(intent="return", slots={"order_id": "SO1234567"}))
    assert "已确认信息" in ctx
    assert "尚缺信息" in ctx          # reason 仍缺
    assert "reason" in ctx


def test_context_holds_prior_answer_when_done():
    s = _state(intent="return", slots={"order_id": "SO1", "reason": "破损"}, stage=Stage.DONE)
    s.answer = "已为您登记"
    ctx = DSTManager.build_system_context(s)
    assert "上一轮结论" in ctx
    assert "已为您登记" in ctx