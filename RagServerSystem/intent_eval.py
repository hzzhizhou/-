"""意图识别评测：对人工标注的问题集跑 rule / hybrid 两种模式，输出准确率与误判明细。

用途：量化意图分类质量，回答「你的意图识别准确率多少」。

与 tests/test_intent_classifier.py 的区别（很重要）：
  - 单测断言「某个句式必须判对」，用例是为规则量身写的（规则改完就补一条），
    只跑它能得到接近 100% 的数字，但那反映的是「回归没坏」而非「泛化能力」；
  - 本评测按两组标注：
      A 组 = 句式与 RULE_PATTERNS 词表高度重合，规则层应当直判；
      B 组 = 同义改写 / 口语化表述，词表大概率覆盖不到，用于暴露规则层的泛化上限。
    分组的差值才是「加一层语义检索」值不值得做的事实依据。

标注说明：全部由人工判定，不是行业金标；存在主观边界的句子已尽量移到 B 组并单独说明。

用法：
  python intent_eval.py          # rule + hybrid（hybrid 会真实调用 LLM，耗时取决于兜底条数）
  python intent_eval.py rule     # 只跑 rule 模式，秒级完成，不产生 LLM 费用
"""
import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
sys.path.append(str(BASE))

from agent.intent_classifier import IntentClassifier  # noqa: E402

INTENTS = ("consult", "return", "refund", "logistics", "order", "complaint", "chat")

# (分组, 期望意图, 问题)
CASES = [
    # ---------------- consult 知识咨询 ----------------
    ("A", "consult", "退货流程是怎样的"),
    ("A", "consult", "退货运费由谁承担"),
    ("A", "consult", "退款多久到账"),
    ("A", "consult", "哪些商品不支持7天无理由退货"),
    ("A", "consult", "退货麻烦吗"),
    ("A", "consult", "7天无理由的7天是从哪天开始算的"),
    ("A", "consult", "商品有质量问题怎么退货"),
    ("A", "consult", "收到的东西和描述不符怎么办"),
    ("A", "consult", "iPhone 15 多少钱"),
    ("A", "consult", "介绍一下你们的 iPhone 15"),
    ("A", "consult", "华为 Mate 60 Pro 参数"),
    ("A", "consult", "支持7天无理由退货吗"),
    ("A", "consult", "退货商品要保持什么状态才能退"),
    ("B", "consult", "手机屏幕碎了能找你们修吗"),
    ("B", "consult", "拆封过的手机还能退回去吗"),
    ("B", "consult", "保修卡丢了还能保修吗"),
    ("B", "consult", "手机进水了保修吗"),
    ("B", "consult", "你们支持以旧换新吗"),
    ("B", "consult", "电子发票可以报销吗"),
    ("B", "consult", "买完之后多久内可以反悔"),
    ("B", "consult", "这个手机颜色有几种可选"),
    ("B", "consult", "配件是原装的吗"),
    ("B", "consult", "你们的售后服务怎么样"),

    # ---------------- return 退货（诉求） ----------------
    ("A", "return", "我要退货"),
    ("A", "return", "我想办理退货，7天无理由"),
    ("A", "return", "帮我把这个订单退掉"),
    ("A", "return", "我要退货，运费谁出"),
    ("A", "return", "我要退货，麻烦帮我办理"),
    ("A", "return", "我不想要了，帮我退掉"),
    ("A", "return", "这个东西我要退换"),
    ("B", "return", "这个手机我不要了，麻烦给我退了吧"),
    ("B", "return", "买重复了，想把多出来的那台处理掉"),
    ("B", "return", "收到货发现不喜欢，想按七天无理由退回"),
    ("B", "return", "货不对板，我要退"),
    ("B", "return", "不满意，想把它退了"),

    # ---------------- refund 退款 ----------------
    ("A", "refund", "请帮我退款"),
    ("A", "refund", "退款进度怎么样了"),
    ("A", "refund", "我的退款到账了吗"),
    ("A", "refund", "退我钱"),
    ("A", "refund", "我要退款，帮我办理"),
    ("A", "refund", "退款申请提交了，帮我看看进度"),
    ("B", "refund", "钱怎么还没打回来"),
    ("B", "refund", "申请退的钱一直没动静"),
    ("B", "refund", "货款什么时候返还给我"),
    ("B", "refund", "上次退货的钱去哪了"),

    # ---------------- logistics 物流 ----------------
    ("A", "logistics", "订单 SO20241120005 的物流到哪了"),
    ("A", "logistics", "我的快递到哪了"),
    ("A", "logistics", "什么时候发货"),
    ("A", "logistics", "物流轨迹给我看一下"),
    ("A", "logistics", "运单号是多少"),
    ("A", "logistics", "这个订单发货了吗"),
    ("B", "logistics", "买的东西现在在哪儿了"),
    ("B", "logistics", "寄出来了吗"),
    ("B", "logistics", "包裹寄到哪一步了"),
    ("B", "logistics", "SF2345678901 这个单子现在到哪儿了"),

    # ---------------- order 订单 ----------------
    ("A", "order", "我的订单状态是什么"),
    ("A", "order", "帮我查一下订单 SO20241120005"),
    ("A", "order", "订单状态都有哪几种"),
    ("A", "order", "我名下有哪些订单"),
    ("A", "order", "我的订单号是多少"),
    ("B", "order", "我买过什么"),
    ("B", "order", "我在你们这儿买过哪些东西"),
    ("B", "order", "历史交易记录在哪查"),
    ("B", "order", "我总共在你们这买了几次"),

    # ---------------- complaint 投诉 ----------------
    ("A", "complaint", "我要投诉你们客服态度太差"),
    ("A", "complaint", "我要举报你们虚假宣传"),
    ("A", "complaint", "我要给差评"),
    ("A", "complaint", "你们客服态度很差"),
    ("B", "complaint", "你们客服处理太慢了，我要个说法"),
    ("B", "complaint", "商品质量太差了，客服也不接电话"),
    ("B", "complaint", "你们这是什么服务，我要找你们领导"),
    ("B", "complaint", "买完就没人管了，太让人失望"),
    ("B", "complaint", "被你们坑了，必须给个解释"),

    # ---------------- chat 闲聊 ----------------
    ("A", "chat", "你好"),
    ("A", "chat", "你好，在吗"),
    ("A", "chat", "谢谢，再见"),
    ("A", "chat", "在吗"),
    ("A", "chat", "辛苦了"),
    ("B", "chat", "客服你好啊，有人在吗"),
    ("B", "chat", "嗯嗯好的"),
    ("B", "chat", "多谢啦，麻烦你了"),
]


def _validate():
    bad = [(g, e, q) for g, e, q in CASES if e not in INTENTS]
    if bad:
        raise SystemExit(f"标注集存在非法意图标签: {bad}")
    dup = {q for _, _, q in CASES}
    if len(dup) != len(CASES):
        raise SystemExit("标注集存在重复问题")


async def run_mode(mode: str, cases):
    """跑一种模式，返回 [(group, expected, question, actual, source, confidence, cost)]"""
    clf = IntentClassifier(mode=mode)
    if mode != "rule":
        from utils.llm_factory import create_llm
        clf = IntentClassifier(mode=mode, llm=create_llm(streaming=False))

    rows = []
    for i, (group, expected, question) in enumerate(cases, 1):
        t0 = time.time()
        try:
            info = await clf.classify(question)
            actual, source = info["intent"], info["source"]
            confidence = info["confidence"]
        except Exception as e:  # 单条失败不中断整轮评测
            actual, source, confidence = f"<异常:{e}>", "error", 0.0
        rows.append((group, expected, question, actual, source,
                     confidence, round(time.time() - t0, 2)))
        if mode != "rule":
            mark = "✓" if actual == expected else "✗"
            print(f"  [{i:>2}/{len(cases)}] {mark} {question[:26]:<28} → {actual} ({source})")
    return rows


def report(mode: str, rows):
    total = len(rows)
    correct = sum(1 for r in rows if r[1] == r[3])

    print("\n" + "=" * 72)
    print(f"【{mode} 模式】")
    print("=" * 72)
    print(f"总体准确率 : {correct}/{total} = {correct / total:.1%}")

    by_group = defaultdict(lambda: [0, 0])
    for group, expected, _, actual, *_ in rows:
        by_group[group][1] += 1
        by_group[group][0] += 1 if expected == actual else 0
    for group in sorted(by_group):
        c, t = by_group[group]
        label = "规则重合句式" if group == "A" else "同义改写    "
        print(f"  {group} 组（{label}）: {c}/{t} = {c / t:.1%}")

    by_intent = defaultdict(lambda: [0, 0])
    for _, expected, _, actual, *_ in rows:
        by_intent[expected][1] += 1
        by_intent[expected][0] += 1 if expected == actual else 0
    print("\n按意图召回：")
    for intent in INTENTS:
        if intent not in by_intent:
            continue
        c, t = by_intent[intent]
        bar = "█" * round(c / t * 12) + "░" * (12 - round(c / t * 12))
        print(f"  {intent:<10} {c:>2}/{t:<2} {bar} {c / t:.0%}")

    if mode != "rule":
        direct = sum(1 for r in rows if r[4] == "rule")
        llm = sum(1 for r in rows if r[4] in ("llm", "rule_lowconf"))
        cost = sum(r[6] for r in rows)
        print(f"\n分流情况   : 规则直判 {direct}/{total}（{direct / total:.0%}）"
              f" | LLM 兜底 {llm}/{total}（{llm / total:.0%}）")
        print(f"总耗时     : {cost:.1f}s（平均 {cost / total:.2f}s/条）")
        fixed, broke = [], []
        for r in rows:
            if r[1] != r[3] and r[4] in ("llm", "rule_lowconf"):
                broke.append(r)
            elif r[1] == r[3] and r[4] in ("llm", "rule_lowconf"):
                fixed.append(r)
        print(f"LLM 兜底效果: 救回 {len(fixed)} 条，改错 {len(broke)} 条")

    mis = [r for r in rows if r[1] != r[3]]
    if mis:
        print(f"\n误判明细（{len(mis)} 条）：")
        for group, expected, question, actual, source, conf, _ in mis:
            print(f"  [{group}] {question}")
            print(f"        期望 {expected} → 实际 {actual}"
                  f"（source={source}, conf={conf}）")
    else:
        print("\n无误判")


async def main():
    _validate()
    groups = defaultdict(int)
    for g, _, _ in CASES:
        groups[g] += 1
    print(f"意图识别评测　标注集 {len(CASES)} 条"
          f"（A 组规则重合 {groups['A']} 条 / B 组同义改写 {groups['B']} 条 / {len(INTENTS)} 个意图）")
    print("标注来源：人工判定，非行业金标")

    only_rule = len(sys.argv) > 1 and sys.argv[1] == "rule"

    print("\n>>> rule 模式（纯规则，不调 LLM）")
    rule_rows = await run_mode("rule", CASES)
    report("rule", rule_rows)

    if only_rule:
        print("\n（跳过 hybrid，未指定则默认两种模式都跑）")
        return

    print("\n>>> hybrid 模式（规则优先 + LLM 兜底）")
    hyb_rows = await run_mode("hybrid", CASES)
    report("hybrid", hyb_rows)

    print("\n" + "=" * 72)
    print("对比小结")
    print("=" * 72)
    rc = sum(1 for r in rule_rows if r[1] == r[3])
    hc = sum(1 for r in hyb_rows if r[1] == r[3])
    print(f"rule   : {rc}/{len(rule_rows)} = {rc / len(rule_rows):.1%}")
    print(f"hybrid : {hc}/{len(hyb_rows)} = {hc / len(hyb_rows):.1%}")
    diff = hc - rc
    print(f"提升   : {diff:+d} 条（{diff / len(rule_rows):+.1%}）")


if __name__ == "__main__":
    asyncio.run(main())
