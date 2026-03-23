from __future__ import annotations

import pathlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from cli_officer.config import ProviderConfig, load_config, save_config
from cli_officer.launcher import bootstrap_workspace, resolve_worker_command
from cli_officer.llm import HeuristicJudge
from cli_officer.models import Decision, DecisionMode, Interrupt
from cli_officer.policy import enforce_thresholds, evaluate_guard
from cli_officer.supervisor import Supervisor


class FakeTmuxClient:
    def __init__(self, panes: list[list[str]]) -> None:
        self.panes = panes
        self.sent: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, str]] = []
        self.splits: list[tuple[str, str]] = []
        self.layouts: list[tuple[str, str]] = []
        self.attached: list[str] = []

    def capture_pane(self, target: str, lines: int = 200) -> list[str]:
        return self.panes.pop(0)

    def send_keys(self, target: str, text: str, enter: bool = True) -> None:
        self.sent.append((target, text))

    def create_session(self, session_name: str, workdir: str, command: str) -> str:
        self.created.append((session_name, workdir, command))
        return "%10"

    def split_window(self, target: str, workdir: str) -> str:
        self.splits.append((target, workdir))
        return "%11"

    def select_layout(self, target: str, layout: str) -> None:
        self.layouts.append((target, layout))

    def attach_session(self, session_name: str) -> None:
        self.attached.append(session_name)


class LowConfidenceJudge:
    def decide(self, interrupt: Interrupt) -> Decision:
        return Decision(True, "low", DecisionMode.AUTO, "yes", 0.5, "uncertain")


class SupervisorTests(unittest.TestCase):
    def test_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = pathlib.Path(tmpdir) / "config.json"
            old_value = os.environ.get("CLI_OFFICER_CONFIG")
            os.environ["CLI_OFFICER_CONFIG"] = str(config_path)
            try:
                save_config(
                    ProviderConfig(
                        supervisor_provider="openai",
                        supervisor_model="gpt-5-mini",
                        supervisor_api_key="secret",
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
            self.assertEqual(loaded.supervisor_provider, "openai")
            self.assertEqual(loaded.supervisor_model, "gpt-5-mini")
            self.assertEqual(loaded.coding_agent, "codex")

    def test_launcher_uses_selected_coding_agent(self) -> None:
        client = FakeTmuxClient([])
        config = ProviderConfig(
            supervisor_provider="openai",
            supervisor_model="gpt-5-mini",
            supervisor_api_key="secret",
            coding_agent="codex",
        )

        result = bootstrap_workspace(client, config, session_name="cli-officer", workdir="/tmp/project")

        self.assertEqual(result.worker_command, "codex")
        self.assertEqual(result.worker_pane, "%10")
        self.assertEqual(result.officer_pane, "%11")
        self.assertEqual(client.created, [("cli-officer", "/tmp/project", "codex")])
        self.assertEqual(client.splits, [("%10", "/tmp/project")])
        self.assertEqual(client.layouts, [("%10", "even-horizontal")])

    def test_resolve_worker_command_for_claude(self) -> None:
        config = ProviderConfig(
            supervisor_provider="anthropic",
            supervisor_model="claude-3-5-sonnet-latest",
            supervisor_api_key="secret",
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

        self.assertEqual(result.action_taken, "suggested")
        self.assertEqual(client.sent, [])
        self.assertEqual(result.decision.mode, DecisionMode.SUGGEST)

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

    def test_reply_is_normalized_to_single_line(self) -> None:
        decision = Decision(True, "low", DecisionMode.AUTO, "yes\n*now*", 0.95, "")

        normalized = enforce_thresholds(decision)

        self.assertEqual(normalized.reply, "yes now")


if __name__ == "__main__":
    unittest.main()
