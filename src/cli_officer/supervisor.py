from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from .detector import detect_interrupt, extract_stalled_candidate
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
    stall_seconds: float = 2.0
    now: object = time.monotonic
    history: list[str] = field(default_factory=list)
    last_change_at: float = field(default_factory=time.monotonic)
    last_handled_signature: str = ""

    def poll_once(self) -> SupervisorResult:
        lines = self.tmux_client.capture_pane(self.target)
        current_time = self.now()
        if lines != self.history:
            self.history = list(lines)
            self.last_change_at = current_time
            return self._evaluate_lines(lines, stalled=False)
        if current_time - self.last_change_at < self.stall_seconds:
            return SupervisorResult(None, None, "noop")
        return self._evaluate_lines(lines, stalled=True)

    def _evaluate_lines(self, lines: list[str], stalled: bool) -> SupervisorResult:
        interrupt = detect_interrupt(lines)
        if not interrupt and stalled:
            interrupt = extract_stalled_candidate(lines)
        if not interrupt:
            result = SupervisorResult(None, None, "observe")
            self._log_result(result)
            return result
        signature = self._interrupt_signature(interrupt)
        if signature == self.last_handled_signature:
            return SupervisorResult(None, None, "noop")

        guarded = None if self.allow_hard_actions else evaluate_guard(interrupt)
        if guarded:
            self.last_handled_signature = signature
            result = SupervisorResult(interrupt, guarded, "blocked-by-policy")
            self._log_result(result)
            return result

        decision = enforce_thresholds(self.judge.decide(interrupt))
        if not decision.needs_reply or not decision.interrupt_detected:
            self.last_handled_signature = signature
            return SupervisorResult(None, None, "noop")
        if decision.mode == DecisionMode.AUTO and decision.reply:
            if not self.dry_run:
                self.tmux_client.send_keys(self.target, decision.reply)
            self.last_handled_signature = signature
            result = SupervisorResult(interrupt, decision, "auto-replied", reply_sent=decision.reply)
            self._log_result(result)
            return result
        if decision.mode == DecisionMode.SUGGEST:
            self.last_handled_signature = signature
            result = SupervisorResult(interrupt, decision, "suggested")
            self._log_result(result)
            return result
        self.last_handled_signature = signature
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
        rationale = result.decision.rationale or "-"
        reply_field = "reply"
        reply_value = result.decision.reply or "-"
        if result.action_taken == "auto-replied":
            reply_field = "sent"
            reply_value = result.reply_sent or "-"
        elif result.action_taken in {"suggested", "blocked", "blocked-by-policy"}:
            reply_field = "candidate"
        print(
            f"[{timestamp}] {result.action_taken} kind={result.interrupt.kind} "
            f"risk={result.decision.risk_level} "
            f"confidence={result.decision.confidence:.2f} "
            f"{reply_field}={reply_value!r} reason={rationale!r} prompt={prompt!r}",
            file=sys.stdout,
            flush=True,
        )

    @staticmethod
    def _interrupt_signature(interrupt: object) -> str:
        return f"{interrupt.kind}|{interrupt.prompt_line}|{interrupt.prompt}"
