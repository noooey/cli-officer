from __future__ import annotations

import time
from dataclasses import dataclass, field

from .detector import detect_interrupt
from .llm import Judge
from .models import DecisionMode, SupervisorResult
from .policy import enforce_thresholds, evaluate_guard


@dataclass(slots=True)
class Supervisor:
    tmux_client: object
    judge: Judge
    target: str
    dry_run: bool = False
    history: list[str] = field(default_factory=list)

    def poll_once(self) -> SupervisorResult:
        lines = self.tmux_client.capture_pane(self.target)
        if lines == self.history:
            return SupervisorResult(None, None, "noop")
        self.history = list(lines)

        interrupt = detect_interrupt(lines)
        if not interrupt:
            return SupervisorResult(None, None, "observe")

        guarded = evaluate_guard(interrupt)
        if guarded:
            return SupervisorResult(interrupt, guarded, "blocked-by-policy")

        decision = enforce_thresholds(self.judge.decide(interrupt))
        if decision.mode == DecisionMode.AUTO and decision.reply:
            if not self.dry_run:
                self.tmux_client.send_keys(self.target, decision.reply)
            return SupervisorResult(interrupt, decision, "auto-replied", reply_sent=decision.reply)
        if decision.mode == DecisionMode.SUGGEST:
            return SupervisorResult(interrupt, decision, "suggested")
        return SupervisorResult(interrupt, decision, "blocked")

    def run_forever(self, interval: float) -> None:
        while True:
            self.poll_once()
            time.sleep(interval)
