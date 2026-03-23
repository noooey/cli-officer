from __future__ import annotations

from dataclasses import dataclass
import shlex
import sys

from .config import ProviderConfig

AGENT_COMMANDS: dict[str, str] = {
    "claude-code": "claude",
    "codex": "codex",
}


@dataclass(slots=True)
class BootstrapResult:
    session_name: str
    worker_pane: str
    officer_pane: str
    worker_command: str


def resolve_worker_command(config: ProviderConfig) -> str:
    try:
        return AGENT_COMMANDS[config.coding_agent]
    except KeyError as exc:
        raise ValueError(f"Unsupported coding agent: {config.coding_agent}") from exc


def build_officer_command(worker_pane: str, interval: float, dry_run: bool) -> str:
    command = [sys.executable, "-m", "cli_officer", "--target", worker_pane, "--interval", str(interval)]
    if dry_run:
        command.append("--dry-run")
    return shlex.join(command)


def bootstrap_workspace(
    tmux_client: object,
    config: ProviderConfig,
    session_name: str,
    workdir: str,
    interval: float,
    dry_run: bool,
) -> BootstrapResult:
    worker_command = resolve_worker_command(config)
    worker_pane = tmux_client.create_session(session_name=session_name, workdir=workdir, command=worker_command)
    officer_command = build_officer_command(worker_pane=worker_pane, interval=interval, dry_run=dry_run)
    officer_pane = tmux_client.split_window(target=worker_pane, workdir=workdir, command=officer_command)
    tmux_client.select_layout(target=worker_pane, layout="even-horizontal")
    tmux_client.select_pane(target=worker_pane)
    return BootstrapResult(
        session_name=session_name,
        worker_pane=worker_pane,
        officer_pane=officer_pane,
        worker_command=worker_command,
    )
