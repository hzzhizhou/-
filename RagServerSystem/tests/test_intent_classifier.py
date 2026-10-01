"""意图分类器单元测试：仅覆盖规则路径（rule 模式不调 LLM），验证关键词打分与置信度。"""
import asyncio

import pytest

from agent.intent_classifier import IntentClassifier, INTENT_CN


def _rule():
    return IntentClassifier(mode="rule")


@pytest.mark.parametrize("question,expected", [
    ("我要退货", "return"),
    ("我想办理退货，7天无理由", "return"),
    ("请帮我退款", "refund"),
    ("退款进度怎么样了", "refund"),
    ("我要投诉你们客服态度太差", "complaint"),
    ("你好，在吗", "chat"),
    ("谢谢，再见", "chat"),
    ("订单 SO20241120005 的物流到哪了", "logistics"),
])
def test_rule_classify_clear_intent(question, expected):
    intent, _ = _rule()._rule_classify(question)
    assert intent == expected


@pytest.mark.parametrize("question", [
    "介绍一下你们的 iPhone 15",
    "iPhone 15 怎么样",
    "小米 15 有什么功能",
    "你们店都卖什么手机",
    "华为 Mate 60 Pro 多少钱",
])
def test_rule_classify_product_intro_goes_consult(question):
    """商品介绍类问题必须落到知识咨询（走商品知识库检索）。

    回归防护：“XX 产品怎么样”必须判成知识咨询走商品知识库，不能被判去联网搜索；
    而“介绍一下X”“X 多少钱”无关键词命中，只能靠 LLM 兜底（多几秒）。
    """
    intent, confidence = _rule()._rule_classify(question)
    assert intent == "consult"
    assert confidence >= 0.5   # 高置信度 → 规则直判，不触发 LLM 兜底


def test_rule_classify_unmatched_falls_to_consult():
    # 无任何关键词命中 → 返回 consult 且置信度极低（触发 LLM 兜底的信号）
    intent, confidence = _rule()._rule_classify("完全无关的乱码 qw312 啊啊")
    assert intent == "consult"
    assert confidence < 0.5


def test_rule_classify_confidence_high_for_single_intent():
    # 单一意图强命中 → 高置信度，直接走规则，不触发 LLM 兜底
    _, confidence = _rule()._rule_classify("我要退货")
    assert confidence >= 0.9


def test_classify_rule_mode_never_calls_llm():
    info = asyncio.run(_rule().classify("今天能发货吗"))
    assert info["source"] == "rule"
    assert info["intent"] in INTENT_CN


@pytest.mark.parametrize("question", [
    "退货运费由谁承担",
    "退款多久到账",
    "哪些商品不支持7天无理由退货",
    "退货流程是怎样的",
    "换货需要付运费吗",
    "退货要几天到账",
    "退款一般几天到账",
    "退货多少天能到账",
    "支持7天无理由退货吗",
    "可以退货吗",
    "是否允许换货",
    "iPhone 15 有现货吗？支持7天无理由退货吗",
])
def test_policy_question_goes_consult_not_return(question):
    """问政策 vs 要办理：命中退货/退款词表但整句在问规则时，必须落到知识咨询。

    回归防护：此前「退货运费由谁承担」被判成 return，直接被确定性退货主流程接管，
    回复「好的，我来帮您办理退货 / 请补充订单号、退货原因」——答非所问。
    """
    intent, confidence = _rule()._rule_classify(question)
    assert intent == "consult"
    assert confidence >= 0.5   # 高置信 → 规则直判，不再花 LLM 兜底（还可能被兜回 return）


@pytest.mark.parametrize("question", [
    "我要退货，运费谁出",
    "帮我把这个订单退掉",
    "请帮我退款",
])
def test_action_request_not_downgraded(question):
    """带办理动作措辞的问句不能被政策守卫误降级（诉求必须仍走确定性主流程）"""
    intent, _ = _rule()._rule_classify(question)
    assert intent in ("return", "refund")


@pytest.mark.parametrize("question", [
    "退货麻烦吗",
    "退款麻烦吗",
    "退货麻不麻烦",
    "退货麻烦不麻烦",
    "退货难吗",
    "退货难不难",
    "退款方便吗",
    "退款方不方便",
    "退货好退吗",
    "退货好不好退",
    "退货复杂吗",
    "换货手续繁琐吗",
])
def test_evaluative_question_goes_consult_not_return(question):
    """评价类问法（问办理体验：难易/繁琐）也必须落到知识咨询。

    回归防护：此前「退货麻烦吗」既无政策词汇、也无条件类的判断动词（支持/能/可以…），
    两道守卫都漏判 → 被判成 return 进确定性退货主流程，回「请补充订单号、退货原因」——答非所问。
    """
    intent, confidence = _rule()._rule_classify(question)
    assert intent == "consult"
    assert confidence >= 0.5   # 高置信 → 规则直判，不再花 LLM 兜底（还可能被兜回 return）


@pytest.mark.parametrize("question", [
    "我要退货，麻烦帮我办理",      # 含诉求动作措辞，不能被评价类守卫误降级
    "退货太麻烦了，我要投诉",      # 陈述抱怨句（无疑问语气）应交由投诉/退货判定，不当咨询
    "麻烦你帮我查下订单",          # "麻烦" 只是客套话，不在评价类疑问句式中
])
def test_evaluative_guard_does_not_overreach(question):
    intent, _ = _rule()._rule_classify(question)
    assert intent != "consult"


def test_classify_force_intent_reuses_and_skips_llm():
    # force_intent 命中合法意图 → 直接复用（source=dst_reuse），不再跑分类
    info = asyncio.run(IntentClassifier(mode="hybrid").classify("确认", force_intent="return"))
    assert info["intent"] == "return"
    assert info["source"] == "dst_reuse"
    assert info["confidence"] == 1.0
    # return 意图优先工具为知识库检索
    assert info["primary_tool"] == "search_knowledge"


@pytest.mark.parametrize("question", [
    "7天无理由的7天是从哪天开始算的",
    "商品有质量问题怎么退货",
    "退货商品要保持什么状态才能退",
    "商家一直不退款怎么办",
    "包邮商品退货，原来的运费退吗",
    "退货运费要不要买家出",
])
def test_question_form_policy_goes_consult_not_return(question):
    """疑问句式（怎么退 / 怎么办 / 哪天算 / 才能退 / 运费退吗）也必须落到知识咨询。

    回归防护：这批问法既无政策词表命中、也无条件类判断动词（支持/能/可以…），
    两道守卫都漏判 → 被判成 return/refund 直接进办理流，回「还需要您补充订单号」——答非所问。
    """
    intent, confidence = _rule()._rule_classify(question)
    assert intent == "consult"
    assert confidence >= 0.5   # 高置信 → 规则直判，不再花 LLM 兜底（还可能被兜回 return）


@pytest.mark.parametrize("question,expected", [
    ("退款进度怎么样了", "refund"),     # 裸「怎么」不算疑问句式：查进度 ≠ 问规则
    ("我的退款到账了吗", "refund"),     # 句末「了吗」不是「退/换…吗」
    ("我要退货", "return"),
])
def test_question_form_guard_does_not_overreach(question, expected):
    intent, _ = _rule()._rule_classify(question)
    assert intent == expected


@pytest.mark.parametrize("question", [
    "收到的东西和描述不符怎么办",
    "商品描述不符怎么办",
])
def test_description_mismatch_goes_consult_not_complaint(question):
    """「与描述不符」是问售后规则（PLAT-009），不是投诉。

    回归防护：此前规则一条都不命中 → 走 LLM 兜底被判成 complaint → 直接建工单，
    用户拿不到任何政策说明。加上关键词后由规则直判 consult。
    """
    intent, confidence = _rule()._rule_classify(question)
    assert intent == "consult"
    assert confidence >= 0.5