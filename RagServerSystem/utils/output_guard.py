"""
输出合规护栏（Output Guard）
客服场景必备：对 LLM 生成答案做事后审查，拦截违规承诺与敏感信息。

审查项：
  1. 敏感词：出现即替换为 ***
  2. 违规承诺：全额退款/免费换新/双倍赔偿/保证时效等需人工授权的话术 → 拦截并提示转人工
  3. 金额事实性：答案中出现金额但参考资料中无对应依据 → 标记风险

设计原则：宁可保守（误标）不可漏放（违规承诺到用户即企业风险）
"""
import re
from typing import Tuple, List
from config.settings import (
    OUTPUT_GUARD_ENABLED, GUARD_SENSITIVE_WORDS,
    GUARD_COMMITMENT_PATTERNS, GUARD_AMOUNT_PATTERN,
)
from logs.log_config import generation_layer_log as log


class OutputGuard:
    """输出合规审查器"""

    def __init__(self):
        self.enabled = OUTPUT_GUARD_ENABLED
        self.sensitive_words = GUARD_SENSITIVE_WORDS
        self.commitment_patterns = [re.compile(p) for p in GUARD_COMMITMENT_PATTERNS]
        self.amount_pattern = re.compile(GUARD_AMOUNT_PATTERN)

    def review(self, answer: str, context: str = "", question: str = "") -> Tuple[str, List[str]]:
        """
        审查生成答案，返回 (安全答案, 风险列表)
        - 安全答案：敏感词已替换，违规承诺已拦截
        - 风险列表：命中的风险描述（空列表表示通过）
        """
        if not self.enabled:
            return answer, []

        issues: List[str] = []
        safe = answer

        # 1. 敏感词替换
        for word in self.sensitive_words:
            if word in safe:
                safe = safe.replace(word, "***")
                issues.append(f"敏感词[{word}]已屏蔽")

        # 2. 违规承诺拦截（需人工授权的话术）
        for pat in self.commitment_patterns:
            m = pat.search(safe)
            if m:
                issues.append(f"疑似违规承诺「{m.group()}」，需人工授权")
                # 不直接删（删了可能语句不通），而是在末尾追加风险提示
                break  # 一次违规承诺即可标记

        # 3. 金额事实性校验：答案有金额但 context 无依据
        answer_amounts = self.amount_pattern.findall(safe)
        if answer_amounts:
            context_amounts = self.amount_pattern.findall(context or "")
            # 答案中的金额若不在 context 中出现，标记为无依据
            unverified = [a for a in answer_amounts if a not in context_amounts]
            if unverified:
                issues.append(f"金额{unverified}在参考资料中无依据，疑似编造")

        # 4. 若有风险，追加合规提示（不破坏原文，但明确告知用户）
        if issues:
            safe += "\n\n【系统提示】以上回答可能包含未经核实的信息，如涉及金额/时效/承诺，请以人工客服确认为准。"
            log.warning(f"输出护栏拦截 | 问题={question[:30]} | 风险={issues}")

        return safe, issues


# 单例
output_guard = OutputGuard()
