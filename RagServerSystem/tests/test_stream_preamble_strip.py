"""流式输出清洗单元测试：验证 ReAct 推理文本不会透给用户。

背景：qwen 偶尔把 ReAct 格式当普通文本输出（不走结构化 tool_calls）且漏掉
"Final Answer:" 标记，此时流式兜底分支会把 Thought/Action/Action Input 原文
推给用户（实测“iPhone 15 怎么样”即触发）。
"""
from shared.constants import strip_react_preamble


def test_strip_removes_thought_and_action_block():
    leaked = (
        "Thought: 用户询问产品怎么样，属于知识咨询类问题，需要检索知识库。\n"
        "\n"
        "Action: search_knowledge\n"
        'Action Input: {"query": "iPhone 15 参数"}\n'
        "\n"
        "您好，为您介绍一下 iPhone 15，售价 4999 元。"
    )
    assert strip_react_preamble(leaked) == "您好，为您介绍一下 iPhone 15，售价 4999 元。"


def test_strip_keeps_plain_answer_untouched():
    plain = "您好，您的订单 SO20241120005 已发货。\n请问还有其他可以帮您的吗？"
    assert strip_react_preamble(plain) == plain


def test_strip_handles_multiline_action_input_json():
    leaked = (
        "Thought: 需要查询订单\n"
        "Action: query_order\n"
        "Action Input: {\n"
        '  "order_id": "SO20241120005"\n'
        "}\n"
        "\n"
        "订单号 SO20241120005 的物流信息如下。"
    )
    assert strip_react_preamble(leaked) == "订单号 SO20241120005 的物流信息如下。"


def test_strip_handles_reasoning_and_answer_on_same_line():
    # 模型把整段 ReAct 挤在一行、正文紧跟在 Action Input 的 JSON 之后
    # （按整行丢弃会把正文一起删掉，实测“你们店都卖什么产品”即触发）
    leaked = ('Thought: 需要查知识库 Action: search_knowledge '
              'Action Input: {"query": "在售手机"} 您好，为您介绍 iPhone 15。')
    assert strip_react_preamble(leaked) == "您好，为您介绍 iPhone 15。"


def test_strip_cuts_after_last_reasoning_block():
    # 工具执行后模型又推理了一轮，正文在其之后
    leaked = (
        'Action Input: {"query": "在售手机"}\n'
        "Observation: 检索到 3 款手机\n"
        "Thought: 现在可以回答了\n"
        "\n"
        "您好，本店在售 3 款手机。"
    )
    assert strip_react_preamble(leaked) == "您好，本店在售 3 款手机。"


def test_strip_returns_empty_when_only_reasoning():
    # 只有推理、没有正文 → 返回空串，由调用方改用兜底话术，避免用户收到空白回复
    only_reasoning = "Thought: 我需要先确认用户意图\nAction: search_knowledge\n"
    assert strip_react_preamble(only_reasoning) == ""