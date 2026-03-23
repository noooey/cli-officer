from __future__ import annotations

import pathlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from cli_officer.config import ProviderConfig, load_config, save_config
from cli_officer.launcher import bootstrap_workspace, build_officer_command, resolve_worker_command
from cli_officer.llm import HeuristicJudge
from cli_officer.models import Decision, DecisionMode, Interrupt
from cli_officer.policy import enforce_thresholds, evaluate_guard
from cli_officer.supervisor import Supervisor


class FakeTmuxClient:
    def __init__(self, panes: list[list[str]]) -> None:
        self.panes = panes
        self.sent: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, str]] = []
        self.splits: list[tuple[str, str, str | None]] = []
        self.layouts: list[tuple[str, str]] = []
        self.selected_panes: list[str] = []
        self.attached: list[str] = []

    def capture_pane(self, target: str, lines: int = 200) -> list[str]:
        return self.panes.pop(0)

    def send_keys(self, target: str, text: str, enter: bool = True) -> None:
        self.sent.append((target, text))

    def create_session(self, session_name: str, workdir: str, command: str) -> str:
        self.created.append((session_name, workdir, command))
        return "%10"

    def split_window(self, target: str, workdir: str, command: str | None = None) -> str:
        self.splits.append((target, workdir, command))
        return "%11"

    def select_layout(self, target: str, layout: str) -> None:
        self.layouts.append((target, layout))

    def select_pane(self, target: str) -> None:
        self.selected_panes.append(target)

    def attach_session(self, session_name: str) -> None:
        self.attached.append(session_name)


class LowConfidenceJudge:
    def decide(self, interrupt: Interrupt) -> Decision:
        return Decision(True, "low", DecisionMode.AUTO, "yes", 0.5, "uncertain")


class NoReplyJudge:
    def __init__(self) -> None:
        self.seen: list[Interrupt] = []

    def decide(self, interrupt: Interrupt) -> Decision:
        self.seen.append(interrupt)
        return Decision(False, "low", DecisionMode.BLOCK, "", 0.0, "No reply needed", False)


class SupervisorTests(unittest.TestCase):
    def test_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = pathlib.Path(tmpdir) / "config.json"
            old_value = os.environ.get("CLI_OFFICER_CONFIG")
            os.environ["CLI_OFFICER_CONFIG"] = str(config_path)
            try:
                save_config(
                    ProviderConfig(
                        officer_provider="openai",
                        officer_model="gpt-5-mini",
                        officer_api_key="secret",
                        coding_agent="codex",
                    )
                )
                loaded = load_config()
            finally:
                if old_value is None:
                    os.environ.pop("CLI_OFFICER_CONFIG", None)
                else:
                    os.environ["CLI_OFFICER_CONFIG"] = old_value

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.officer_provider, "openai")
            self.assertEqual(loaded.officer_model, "gpt-5-mini")
            self.assertEqual(loaded.coding_agent, "codex")

    def test_launcher_uses_selected_coding_agent(self) -> None:
        client = FakeTmuxClient([])
        config = ProviderConfig(
            officer_provider="openai",
            officer_model="gpt-5-mini",
            officer_api_key="secret",
            coding_agent="codex",
        )

        result = bootstrap_workspace(
            client,
            config,
            session_name="cli-officer",
            workdir="/tmp/project",
            interval=1.0,
            dry_run=False,
            allow_hard_actions=False,
        )
        

        self.assertEqual(result.worker_command, "codex")
        self.assertEqual(result.worker_pane, "%10")
        self.assertEqual(result.officer_pane, "%11")
        self.assertEqual(client.created, [("cli-officer", "/tmp/project", "codex")])
        self.assertEqual(client.splits, [("%10", "/tmp/project", build_officer_command("%10", 1.0, False, False))])
        self.assertEqual(client.layouts, [("%10", "even-horizontal")])
        self.assertEqual(client.selected_panes, ["%10"])

    def test_build_officer_command_targets_worker_pane(self) -> None:
        command = build_officer_command(worker_pane="%10", interval=1.0, dry_run=True, allow_hard_actions=True)

        self.assertIn("--target %10", command)
        self.assertIn("--interval 1.0", command)
        self.assertIn("--dry-run", command)
        self.assertIn("--hard", command)

    def test_resolve_worker_command_for_claude(self) -> None:
        config = ProviderConfig(
            officer_provider="anthropic",
            officer_model="claude-3-5-sonnet-latest",
            officer_api_key="secret",
            coding_agent="claude-code",
        )

        command = resolve_worker_command(config)

        self.assertEqual(command, "claude")

    def test_fake_tmux_can_record_attach(self) -> None:
        client = FakeTmuxClient([])

        client.attach_session("cli-officer")

        self.assertEqual(client.attached, ["cli-officer"])

    def test_auto_reply_for_safe_confirmation(self) -> None:
        client = FakeTmuxClient([["Build complete", "Continue? [y/n]"]])
        supervisor = Supervisor(client, HeuristicJudge(), "%1", dry_run=False)

        result = supervisor.poll_once()

        self.assertEqual(result.action_taken, "auto-replied")
        self.assertEqual(result.reply_sent, "yes")
        self.assertEqual(client.sent, [("%1", "yes")])

    def test_policy_blocks_dangerous_prompt(self) -> None:
        client = FakeTmuxClient([["About to deploy to production", "Continue? [y/n]"]])
        supervisor = Supervisor(client, HeuristicJudge(), "%1", dry_run=False)

        result = supervisor.poll_once()

        self.assertEqual(result.action_taken, "blocked-by-policy")
        self.assertEqual(client.sent, [])
        self.assertEqual(result.decision.mode, DecisionMode.BLOCK)

    def test_low_confidence_downgrades_to_suggest(self) -> None:
        client = FakeTmuxClient([["Operation failed", "Retry? [y/n]"]])
        supervisor = Supervisor(client, LowConfidenceJudge(), "%1", dry_run=False)

        result = supervisor.poll_once()

        self.assertEqual(result.action_taken, "auto-replied")
        self.assertEqual(client.sent, [("%1", "yes")])
        self.assertEqual(result.decision.mode, DecisionMode.AUTO)

    def test_natural_language_approval_auto_replies(self) -> None:
        client = FakeTmuxClient(
            [[
                "Pragmatically, this is ready.",
                "원하면 다음 턴에 이걸 바로 QA 체크리스트 형태로 바꿔드리겠습니다.",
            ]]
        )
        supervisor = Supervisor(client, HeuristicJudge(), "%1", dry_run=False)

        result = supervisor.poll_once()

        self.assertEqual(result.action_taken, "auto-replied")
        self.assertEqual(result.reply_sent, "yes")
        self.assertEqual(client.sent, [("%1", "yes")])

    def test_bulleted_choice_picks_first_option(self) -> None:
        client = FakeTmuxClient(
            [[
                "원하시면 다음 단계로 바로 이어서",
                "- QA 체크리스트 형태",
                "- 테스트케이스 표 형태",
                "- Playwright용 시나리오 형태",
                "중 하나로 바꿔서 정리하겠습니다.",
            ]]
        )
        supervisor = Supervisor(client, HeuristicJudge(), "%1", dry_run=False)

        result = supervisor.poll_once()

        self.assertEqual(result.action_taken, "auto-replied")
        self.assertEqual(result.reply_sent, "QA 체크리스트 형태")
        self.assertEqual(client.sent, [("%1", "QA 체크리스트 형태")])

    def test_stalled_worker_retries_interrupt_detection(self) -> None:
        times = iter([0.0, 3.0])
        client = FakeTmuxClient([
            ["Would you like me to continue with the checklist?"],
            ["Would you like me to continue with the checklist?"],
        ])
        supervisor = Supervisor(client, HeuristicJudge(), "%1", dry_run=False, now=lambda: next(times), stall_seconds=2.0)

        first = supervisor.poll_once()
        second = supervisor.poll_once()

        self.assertEqual(first.action_taken, "auto-replied")
        self.assertEqual(second.action_taken, "noop")
        self.assertEqual(client.sent, [("%1", "yes")])

    def test_stalled_worker_can_skip_non_prompt_context(self) -> None:
        times = iter([0.0, 3.0])
        judge = NoReplyJudge()
        client = FakeTmuxClient([
            ["Status update", "The migration completed successfully."],
            ["Status update", "The migration completed successfully."],
        ])
        supervisor = Supervisor(client, judge, "%1", dry_run=False, now=lambda: next(times), stall_seconds=2.0)

        first = supervisor.poll_once()
        second = supervisor.poll_once()

        self.assertEqual(first.action_taken, "observe")
        self.assertEqual(second.action_taken, "observe")
        self.assertEqual(client.sent, [])
        self.assertEqual(judge.seen, [])

    def test_stalled_worker_ignores_plain_manual_input_like_line(self) -> None:
        times = iter([0.0, 3.0])
        judge = NoReplyJudge()
        client = FakeTmuxClient([
            ["status summary", "이걸 qa 체크리스트로 바꿔줘"],
            ["status summary", "이걸 qa 체크리스트로 바꿔줘"],
        ])
        supervisor = Supervisor(client, judge, "%1", dry_run=False, now=lambda: next(times), stall_seconds=2.0)

        first = supervisor.poll_once()
        second = supervisor.poll_once()

        self.assertEqual(first.action_taken, "observe")
        self.assertEqual(second.action_taken, "observe")
        self.assertEqual(client.sent, [])
        self.assertEqual(judge.seen, [])

    def test_recent_sent_reply_echo_is_ignored_on_next_capture(self) -> None:
        times = iter([0.0, 1.0, 3.5])
        client = FakeTmuxClient([
            ["Continue? [y/n]"],
            ["Continue? [y/n]", "yes"],
            ["Continue? [y/n]", "yes"],
        ])
        supervisor = Supervisor(client, HeuristicJudge(), "%1", dry_run=False, now=lambda: next(times), stall_seconds=2.0)

        first = supervisor.poll_once()
        second = supervisor.poll_once()
        third = supervisor.poll_once()

        self.assertEqual(first.action_taken, "auto-replied")
        self.assertEqual(second.action_taken, "noop")
        self.assertEqual(third.action_taken, "noop")
        self.assertEqual(client.sent, [("%1", "yes")])

    def test_guard_detects_git_push(self) -> None:
        interrupt = Interrupt(
            prompt="git push origin main\nProceed? [y/n]",
            prompt_line="Proceed? [y/n]",
            context=["git push origin main", "Proceed? [y/n]"],
            kind="confirm",
        )

        decision = evaluate_guard(interrupt)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.mode, DecisionMode.BLOCK)

    def test_guard_detects_sandbox_bypass(self) -> None:
        interrupt = Interrupt(
            prompt="command failed; retry without sandbox?\n1. Yes\n2. No",
            prompt_line="command failed; retry without sandbox?",
            context=["command failed; retry without sandbox?", "1. Yes", "2. No"],
            kind="confirm",
        )

        decision = evaluate_guard(interrupt)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.mode, DecisionMode.BLOCK)
        self.assertEqual(decision.risk_level, "sandbox-bypass")

    def test_hard_mode_skips_policy_guard(self) -> None:
        client = FakeTmuxClient([["command failed; retry without sandbox?", "Continue? [y/n]"]])
        supervisor = Supervisor(client, HeuristicJudge(), "%1", dry_run=False, allow_hard_actions=True)

        result = supervisor.poll_once()

        self.assertNotEqual(result.action_taken, "blocked-by-policy")

    def test_hard_mode_auto_approves_suggested_reply(self) -> None:
        client = FakeTmuxClient([["Operation failed", "Retry? [y/n]"]])
        supervisor = Supervisor(client, LowConfidenceJudge(), "%1", dry_run=False, allow_hard_actions=True)

        result = supervisor.poll_once()

        self.assertEqual(result.action_taken, "auto-replied")
        self.assertEqual(result.reply_sent, "yes")
        self.assertEqual(client.sent, [("%1", "yes")])

    def test_reply_is_normalized_to_single_line(self) -> None:
        decision = Decision(True, "low", DecisionMode.AUTO, "yes\n*now*", 0.95, "")

        normalized = enforce_thresholds(decision)

        self.assertEqual(normalized.reply, "yes now")


if __name__ == "__main__":
    unittest.main()
