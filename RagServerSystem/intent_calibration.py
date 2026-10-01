"""意图置信度校准分析。

要解决的问题：规则层的 confidence = best_score / sum(scores) 是「最高分占全部命中分的
比例」，是个 0~1 的无量纲占比，**不是「判对的概率」**。实测反例：「上次退货的钱去哪了」
被判成 return（错），但占比算出 1.0——看上去最有把握的反而判错。

本脚本量化这个偏差，产出三样可落地的东西：
  1. 可靠性表（reliability table）：按原始占比分桶，看桶内实测准确率 vs 平均置信度
  2. ECE（期望校准误差）：把偏差压成一个标量，越接近 0 越说明「置信度≈概率」
  3. 阈值扫描：不同 INTENT_LLM_FALLBACK_CONFIDENCE 下各有多少条规则直判、直判准确率多少

数据来源：直接复用 intent_eval.CASES 的 81 条人工标注集（不重复维护一份标注数据）。

用法：
  python intent_calibration.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.append(str(BASE))

from agent.intent_classifier import IntentClassifier  # noqa: E402
from intent_eval import CASES  # noqa: E402

# 按原始占比（confidence）分桶
RAW_BINS = [(0.0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
# 按 top_score 绝对量级分桶：用于判断「命中强度」是否比「占比」更有信息量
TOP_BINS = [(0.0, 0.75), (0.75, 1.5), (1.5, 2.5), (2.5, 3.5), (3.5, 999.0)]
# 候选兜底阈值（当前 settings.INTENT_LLM_FALLBACK_CONFIDENCE = 0.5）
THRESHOLDS = [0.1, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
CURRENT_THRESHOLD = 0.5


def collect():
    """跑规则层，逐条保留原始占比 / 最高分 / 次高分，不调用 LLM。"""
    clf = IntentClassifier(mode="rule")
    rows = []
    for group, expected, question in CASES:
        scores = clf.rule_scores(question)
        ordered = sorted(scores.values(), reverse=True)
        top = ordered[0] if ordered else 0.0
        second = ordered[1] if len(ordered) > 1 else 0.0
        actual, raw = clf._rule_classify(question)
        rows.append({
            "group": group, "expected": expected, "question": question,
            "actual": actual, "raw": raw, "top": top, "second": second,
            "margin": top - second, "correct": actual == expected,
        })
    return rows


def bucketize(rows, key, bins):
    """按 bins 切分，返回每桶 [Lo, Hi, n, 平均预测值, 实测准确率, 偏差]。"""
    table = []
    for lo, hi in bins:
        sel = [r for r in rows if lo <= r[key] < hi]
        if not sel:
            table.append((lo, hi, 0, None, None, None))
            continue
        n = len(sel)
        avg = sum(r[key] for r in sel) / n
        acc = sum(1 for r in sel if r["correct"]) / n
        table.append((lo, hi, n, avg, acc, acc - avg))
    return table


def ece(rows, table):
    """期望校准误差 = Σ (n_i/N) · |acc_i − avg_conf_i|"""
    n_total = len(rows)
    return sum(n / n_total * abs(acc - avg)
               for _, _, n, avg, acc, _ in table if n)


def monotonic_ok(table):
    accs = [acc for _, _, n, _, acc, _ in table if n]
    return all(a <= b for a, b in zip(accs, accs[1:]))


def print_table(title, table, key_cn):
    print(f"\n【{title}】")
    print(f"  {'区间':<16}{'条数':>5}{'平均' + key_cn:>12}{'实测准确率':>12}{'偏差':>10}")
    for lo, hi, n, avg, acc, gap in table:
        if not n:
            print(f"  [{lo:.2f}, {hi:.2f})  {'0':>5}{'—':>12}{'—':>12}{'—':>10}")
            continue
        bar = "█" * round(acc * 10) + "░" * (10 - round(acc * 10))
        print(f"  [{lo:.2f}, {hi:.2f})  {n:>5}{avg:>12.3f}{acc:>10.1%} {bar} {gap:>+7.3f}")


def pava_bins(table):
    """Pool Adjacent Violators：把不单调的相邻桶合并成单调不减的校准值。

    返回 [(lo, hi, n, 校准值)]，合并后的区间取被合并各桶的真实跨度，
    否则会把合并块的概率贴到某个子区间上（错位）。
    """
    blocks = [{"lo": lo, "hi": hi, "n": n, "acc": acc}
              for lo, hi, n, _, acc, _ in table if n]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i]["acc"] > blocks[i + 1]["acc"]:
            a, b = blocks[i], blocks[i + 1]
            n = a["n"] + b["n"]
            blocks[i:i + 2] = [{
                "lo": a["lo"], "hi": b["hi"], "n": n,
                "acc": (a["n"] * a["acc"] + b["n"] * b["acc"]) / n,
            }]
            i = max(i - 1, 0)
        else:
            i += 1
    return [(b["lo"], b["hi"], b["n"], b["acc"]) for b in blocks]


def main():
    rows = collect()
    n_total = len(rows)
    n_correct = sum(1 for r in rows if r["correct"])
    print("=" * 74)
    print(f"意图置信度校准分析　标注集 {n_total} 条（复用 intent_eval.CASES，规则层，无 LLM 费用）")
    print("=" * 74)
    print(f"规则层整体准确率: {n_correct}/{n_total} = {n_correct / n_total:.1%}")

    # ---------- 表1：按原始占比 ----------
    raw_table = bucketize(rows, "raw", RAW_BINS)
    print_table("表1 可靠性表（按原始占比 confidence 分桶）", raw_table, "占比")
    print(f"\n  ECE（期望校准误差）= {ece(rows, raw_table):.3f}"
          f"　（0 = 置信度就是准确率；越大约说明置信度越不可信）")
    print(f"  桶间准确率单调递增: {'是' if monotonic_ok(raw_table) else '否'}")

    # ---------- 表2：按 top_score 绝对量级 ----------
    top_table = bucketize(rows, "top", TOP_BINS)
    print_table("表2 按最高分绝对量级 top_score 分桶（考验「命中强度」是否比「占比」有信息量）",
                top_table, "top")
    print(f"\n  桶间准确率单调递增: {'是' if monotonic_ok(top_table) else '否'}"
          f"（top_score 不是概率，看桶准确率的区分度即可）")

    # ---------- 表3：阈值扫描 ----------
    print("\n【表3 兜底阈值扫描（INTENT_LLM_FALLBACK_CONFIDENCE）】")
    print(f"  {'阈值':>6}{'规则直判':>10}{'其中判错':>10}{'直判准确率':>12}{'LLM兜底':>10}{'兜底率':>10}")
    for t in THRESHOLDS:
        direct = [r for r in rows if r["raw"] >= t]
        fallback = n_total - len(direct)
        err = sum(1 for r in direct if not r["correct"])
        acc = f"{(len(direct) - err) / len(direct):.1%}" if direct else "—"
        mark = "  ← 当前" if t == CURRENT_THRESHOLD else ""
        print(f"  {t:>6.2f}{len(direct):>10}{err:>10}{acc:>12}{fallback:>10}"
              f"{fallback / n_total:>9.1%}{mark}")

    # ---------- 表4：误判明细（看高置信度判错） ----------
    mis = [r for r in rows if not r["correct"]]
    print(f"\n【表4 误判明细 {len(mis)} 条】")
    for r in sorted(mis, key=lambda x: -x["raw"]):
        print(f"  [{r['group']}] {r['question']}")
        print(f"        期望 {r['expected']} → 实际 {r['actual']}"
              f"　raw={r['raw']}　top={r['top']}　second={r['second']}　margin={r['margin']}")

    # ---------- 可粘贴的校准表 ----------
    merged = pava_bins(raw_table)
    n_raw_bins = len([b for b in raw_table if b[2]])
    print("\n" + "=" * 74)
    print("校准表（PAVA 单调化后，若真要引入 CALIBRATION_BINS 就用这段）")
    print("=" * 74)
    print("CALIBRATION_BINS = [")
    for lo, hi, n, acc in merged:
        print(f"    ({lo:.2f}, {hi:.2f}, {acc:.3f}),   # n={n} 实测准确率 {acc:.1%}")
    print("]")
    if len(merged) < n_raw_bins:
        print(f"# 注：PAVA 把 {n_raw_bins} 个桶合并成 {len(merged)} 个，说明原始占比在这些区间不单调，"
              "单桶实测准确率不能直接当概率用。")
    print("# 但注意：confidence 目前只被『>= 阈值』这一个二值闸门和一行日志/prompt 文本消费，"
          "换掉打印的数字不会改变任何一条路由（见表3）。")


if __name__ == "__main__":
    main()
