from __future__ import annotations

import re

from .models import Decision, DecisionMode, Interrupt

DANGEROUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deletion", re.compile(r"\b(?:delete|remove|rm\b|destroy|drop table|truncate)\b", re.IGNORECASE)),
    ("git-history", re.compile(r"\b(?:git push|merge|rebase|reset --hard|push --force|force[- ]push)\b", re.IGNORECASE)),
    ("credential", re.compile(r"\b(?:token|password|secret|credential|api key|ssh key)\b", re.IGNORECASE)),
    ("privilege", re.compile(r"\b(?:sudo|root permission|elevated permission|administrator)\b", re.IGNORECASE)),
    ("sandbox-bypass", re.compile(r"\b(?:without sandbox|retry without sandbox|bypass sandbox|disable sandbox)\b", re.IGNORECASE)),
    ("external-side-effect", re.compile(r"\b(?:deploy|production|publish|send request|call api|payment|billing)\b", re.IGNORECASE)),
)


def evaluate_guard(interrupt: Interrupt) -> Decision | None:
    text = "\n".join(interrupt.context)
    for risk_level, pattern in DANGEROUS_PATTERNS:
        if pattern.search(text):
            return Decision(
                interrupt_detected=True,
                risk_level=risk_level,
                mode=DecisionMode.BLOCK,
                reply="",
                confidence=1.0,
                rationale=f"Matched hard-block pattern: {risk_level}",
            )
    return None


def normalize_reply(reply: str) -> str:
    one_line = " ".join(reply.strip().splitlines()).strip()
    return one_line.replace("`", "").replace("*", "")


def enforce_thresholds(decision: Decision) -> Decision:
    reply = normalize_reply(decision.reply)
    mode = decision.mode
    if decision.confidence < 0.4:
        mode = DecisionMode.BLOCK
    elif decision.confidence < 0.7 and mode == DecisionMode.AUTO:
        mode = DecisionMode.SUGGEST
    return Decision(
        interrupt_detected=decision.interrupt_detected,
        risk_level=decision.risk_level,
        mode=mode,
        reply=reply,
        confidence=decision.confidence,
        rationale=decision.rationale,
    )
