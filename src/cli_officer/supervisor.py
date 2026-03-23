from __future__ import annotations

import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field

from .detector import detect_interrupt, extract_stalled_candidate
from .llm import HeuristicJudge, Judge
from .models import SupervisorResult
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
    recent_sent_replies: deque[str] = field(default_factory=lambda: deque(maxlen=8))

    def poll_once(self) -> SupervisorResult:
        lines = self.tmux_client.capture_pane(self.target)
        lines = self._strip_recent_sent_echoes(lines)
        lines = self._strip_trailing_input_buffer(lines)
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
            result = SupervisorResult(interrupt, guarded, "suggested")
            self._log_result(result)
            return result

        heuristic_kinds = {"confirm", "approval", "retry"}
        active_judge = (
            HeuristicJudge()
            if self.allow_hard_actions or interrupt.kind in heuristic_kinds
            else self.judge
        )
        decision = enforce_thresholds(active_judge.decide(interrupt))
        if not decision.needs_reply or not decision.interrupt_detected:
            self.last_handled_signature = signature
            return SupervisorResult(None, None, "noop")
        if not decision.reply:
            self.last_handled_signature = signature
            return SupervisorResult(None, None, "noop")
        if not self.dry_run:
            self.tmux_client.send_keys(self.target, decision.reply)
            self._remember_sent_reply(decision.reply)
        self.last_handled_signature = signature
        result = SupervisorResult(interrupt, decision, "auto-replied", reply_sent=decision.reply)
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
        elif result.action_taken == "suggested":
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

    def _remember_sent_reply(self, reply: str) -> None:
        normalized = " ".join(reply.strip().split())
        if normalized:
            self.recent_sent_replies.append(normalized)

    def _strip_recent_sent_echoes(self, lines: list[str]) -> list[str]:
        if not self.recent_sent_replies:
            return lines
        trimmed = list(lines)
        while trimmed:
            candidate = trimmed[-1].strip()
            if not candidate:
                trimmed.pop()
                continue
            normalized = " ".join(candidate.split())
            if normalized in self.recent_sent_replies:
                trimmed.pop()
                continue
            break
        return trimmed

    @staticmethod
    def _strip_trailing_input_buffer(lines: list[str]) -> list[str]:
        trimmed = list(lines)
        while trimmed:
            candidate = trimmed[-1].rstrip()
            stripped = candidate.strip()
            if not stripped:
                trimmed.pop()
                continue
            if stripped.startswith("› ") or stripped.startswith("> "):
                rest = stripped[2:].lstrip()
                if re.match(r"\d+[.)]\s", rest):
                    break  # numbered menu option, not an input hint
                trimmed.pop()
                continue
            if stripped in {"│", "|"}:
                trimmed.pop()
                continue
            break
        return trimmed

