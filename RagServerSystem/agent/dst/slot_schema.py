"""
DST 槽位 Schema 与阶段定义

为每个业务意图定义"需要收集哪些槽位"：
  - required=True  关键槽位：缺则停留在 COLLECTING，Agent 应向用户澄清
  - required=False 可选槽位：补充信息，不阻塞流程

供两处使用：
  - slot_extractor：告诉 LLM 每个意图可以提取哪些槽位（白名单）
  - dst_manager   ：生成"已收集 / 待补项"提示注入主 Agent
"""
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Pattern


class Stage(str, Enum):
    """会话阶段：缺关键槽位→澄清；槽位齐→确认；调工具→执行；完整答复→完成"""
    COLLECTING = "COLLECTING"   # 关键槽位缺失，需向用户澄清
    CONFIRMING = "CONFIRMING"   # 关键槽位已齐，等待确认或组织回答
    EXECUTING = "EXECUTING"     # 正在调用业务工具
    DONE = "DONE"               # 已给出完整答复


@dataclass
class SlotDef:
    """单个槽位定义"""
    required: bool = False          # 是否为该意图的关键槽位
    desc: str = ""                  # 槽位含义（供 prompt 描述）
    pattern: Optional[Pattern] = None  # 可选的格式校验正则


# ---------- 槽位格式校验正则 ----------
# 订单号：SO 开头或纯字母数字，长度>=6
ORDER_ID_RE = re.compile(r"SO[\dA-Za-z]+|[\dA-Za-z]{6,}")
# 金额：必须带 ¥ 前缀或 元 后缀，避免误匹配纯数字（如订单号）
AMOUNT_RE = re.compile(r"¥\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*元")


# ---------- 意图 → 槽位约束表----------
SLOT_SCHEMA: Dict[str, Dict[str, SlotDef]] = {
    "return": {
        "order_id": SlotDef(required=True, desc="订单号，形如 SO20241120005", pattern=ORDER_ID_RE),
        "reason": SlotDef(required=True, desc="退货原因，如商品破损/屏幕碎裂/进水"),
        "product": SlotDef(required=False, desc="商品名称"),
        "amount": SlotDef(required=False, desc="退款金额", pattern=AMOUNT_RE),
    },
    "refund": {
        "order_id": SlotDef(required=True, desc="订单号", pattern=ORDER_ID_RE),
        "amount": SlotDef(required=False, desc="退款金额", pattern=AMOUNT_RE),
        "reason": SlotDef(required=False, desc="退款原因"),
    },
    "logistics": {
        "order_id": SlotDef(required=True, desc="订单号", pattern=ORDER_ID_RE),
    },
    "order": {
        "order_id": SlotDef(required=True, desc="订单号", pattern=ORDER_ID_RE),
    },
    "complaint": {
        "reason": SlotDef(required=True, desc="投诉原因"),
        "order_id": SlotDef(required=False, desc="涉及的订单号", pattern=ORDER_ID_RE),
        "contact": SlotDef(required=False, desc="联系方式"),
    },
    # 以下意图无业务槽位（知识咨询/闲聊）
    "consult": {},
    "chat": {},
}


def required_slots(intent: str) -> set:
    """返回某意图的必填槽位集合，name 为槽位名称，s 为槽位定义对象"""
    return {name for name, s in SLOT_SCHEMA.get(intent, {}).items() if s.required}

def all_slots(intent: str) -> set:
    """返回某意图的全部槽位集合"""
    return set(SLOT_SCHEMA.get(intent, {}).keys())


def slot_prompt_desc(intent: str) -> str:
    """生成该意图可提取槽位的 prompt 描述文本（供 slot_extractor 使用）"""
    slots = SLOT_SCHEMA.get(intent, {})
    if not slots:
        return ""
    return "\n".join(f"- {name}: {s.desc}（{'必填' if s.required else '可选'}）"
                     for name, s in slots.items())
