from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from .config import ensure_config
from .launcher import bootstrap_workspace
from .llm import build_judge
from .supervisor import Supervisor
from .tmux import TmuxClient, TmuxError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervise a worker pane and auto-handle safe interrupts.")
    parser.add_argument("--target", help="tmux target pane, e.g. %%1 or session:window.pane")
    parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle")
    parser.add_argument("--dry-run", action="store_true", help="Do not inject replies into tmux")
    parser.add_argument("--init", action="store_true", help="Run provider setup and exit")
    parser.add_argument("--launch", action="store_true", help="Create a 2-pane tmux session and launch the selected coding agent")
    parser.add_argument("--attach", action="store_true", help="Attach to the launched tmux session after bootstrapping it")
    parser.add_argument("--session-name", default="cli-officer", help="tmux session name for --launch")
    parser.add_argument("--workdir", default=os.getcwd(), help="Working directory for --launch")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = ensure_config()
    if args.init:
        print(
            json.dumps(
                {
                    "officer_provider": config.officer_provider,
                    "officer_model": config.officer_model,
                    "coding_agent": config.coding_agent,
                }
            )
        )
        return 0
    if shutil.which("tmux") is None:
        print("tmux is required but was not found in PATH. See README.md for installation.", file=sys.stderr)
        return 2
    tmux_client = TmuxClient()
    target = args.target
    try:
        if args.launch:
            bootstrapped = bootstrap_workspace(
                tmux_client=tmux_client,
                config=config,
                session_name=args.session_name,
                workdir=args.workdir,
            )
            target = bootstrapped.worker_pane
            print(
                json.dumps(
                    {
                        "session_name": bootstrapped.session_name,
                        "worker_pane": bootstrapped.worker_pane,
                        "officer_pane": bootstrapped.officer_pane,
                        "worker_command": bootstrapped.worker_command,
                        "attach_command": f"tmux attach-session -t {bootstrapped.session_name}",
                    }
                )
            )
            if args.attach:
                tmux_client.attach_session(bootstrapped.session_name)
                return 0
        if not target:
            print("--target is required unless --launch is used.", file=sys.stderr)
            return 2
        supervisor = Supervisor(
            tmux_client=tmux_client,
            judge=build_judge(config),
            target=target,
            dry_run=args.dry_run,
            log_events=True,
        )
        if args.once:
            result = supervisor.poll_once()
            payload = {
                "action_taken": result.action_taken,
                "reply_sent": result.reply_sent,
                "interrupt": result.interrupt.prompt_line if result.interrupt else None,
                "decision": (
                    {
                        "mode": result.decision.mode.value,
                        "risk_level": result.decision.risk_level,
                        "reply": result.decision.reply,
                        "confidence": result.decision.confidence,
                    }
                    if result.decision
                    else None
                ),
            }
            print(json.dumps(payload))
            return 0
        supervisor.run_forever(args.interval)
        return 0
    except TmuxError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
