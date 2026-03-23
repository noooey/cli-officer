from __future__ import annotations

import sys
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
    allow_hard_actions: bool = False
    log_events: bool = True
    history: list[str] = field(default_factory=list)

    def poll_once(self) -> SupervisorResult:
        lines = self.tmux_client.capture_pane(self.target)
        if lines == self.history:
            return SupervisorResult(None, None, "noop")
        self.history = list(lines)

        interrupt = detect_interrupt(lines)
        if not interrupt:
            result = SupervisorResult(None, None, "observe")
            self._log_result(result)
            return result

        guarded = None if self.allow_hard_actions else evaluate_guard(interrupt)
        if guarded:
            result = SupervisorResult(interrupt, guarded, "blocked-by-policy")
            self._log_result(result)
            return result

        decision = enforce_thresholds(self.judge.decide(interrupt))
        if decision.mode == DecisionMode.AUTO and decision.reply:
            if not self.dry_run:
                self.tmux_client.send_keys(self.target, decision.reply)
            result = SupervisorResult(interrupt, decision, "auto-replied", reply_sent=decision.reply)
            self._log_result(result)
            return result
        if decision.mode == DecisionMode.SUGGEST:
            result = SupervisorResult(interrupt, decision, "suggested")
            self._log_result(result)
            return result
        result = SupervisorResult(interrupt, decision, "blocked")
        self._log_result(result)
        return result

    def run_forever(self, interval: float) -> None:
        while True:
            self.poll_once()
            time.sleep(interval)

    def _log_result(self, result: SupervisorResult) -> None:
        if not self.log_events or result.action_taken == "noop":
            return
        timestamp = time.strftime("%H:%M:%S")
        if result.interrupt is None or result.decision is None:
            return
        prompt = result.interrupt.prompt_line.replace("\n", " ").strip()
        reply = result.reply_sent or result.decision.reply or "-"
        rationale = result.decision.rationale or "-"
        print(
            f"[{timestamp}] {result.action_taken} kind={result.interrupt.kind} "
            f"risk={result.decision.risk_level} "
            f"confidence={result.decision.confidence:.2f} "
            f"reply={reply!r} reason={rationale!r} prompt={prompt!r}",
            file=sys.stdout,
            flush=True,
        )
