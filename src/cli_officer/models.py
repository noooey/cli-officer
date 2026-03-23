from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DecisionMode(str, Enum):
    AUTO = "auto"
    SUGGEST = "suggest"
    BLOCK = "block"


@dataclass(slots=True)
class Interrupt:
    prompt: str
    prompt_line: str
    context: list[str] = field(default_factory=list)
    kind: str = "unknown"


@dataclass(slots=True)
class Decision:
    interrupt_detected: bool
    risk_level: str
    mode: DecisionMode
    reply: str
    confidence: float
    rationale: str = ""
    needs_reply: bool = True


@dataclass(slots=True)
class SupervisorResult:
    interrupt: Interrupt | None
    decision: Decision | None
    action_taken: str
    reply_sent: str = ""
