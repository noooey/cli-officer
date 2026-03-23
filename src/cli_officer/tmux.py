from __future__ import annotations

import subprocess
from dataclasses import dataclass


class TmuxError(RuntimeError):
    pass


@dataclass(slots=True)
class TmuxClient:
    binary: str = "tmux"

    def _run(self, command: list[str]) -> str:
        completed = subprocess.run(command, check=False, text=True, capture_output=True)
        if completed.returncode != 0:
            raise TmuxError(completed.stderr.strip() or "tmux command failed")
        return completed.stdout.strip()

    def create_session(self, session_name: str, workdir: str, command: str) -> str:
        return self._run(
            [
                self.binary,
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                session_name,
                "-c",
                workdir,
                command,
            ]
        )

    def split_window(self, target: str, workdir: str) -> str:
        return self._run(
            [
                self.binary,
                "split-window",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                target,
                "-h",
                "-c",
                workdir,
            ]
        )

    def select_layout(self, target: str, layout: str) -> None:
        self._run([self.binary, "select-layout", "-t", target, layout])

    def select_pane(self, target: str) -> None:
        self._run([self.binary, "select-pane", "-t", target])

    def attach_session(self, session_name: str) -> None:
        self._run([self.binary, "attach-session", "-t", session_name])

    def capture_pane(self, target: str, lines: int = 200) -> list[str]:
        return self._run([self.binary, "capture-pane", "-p", "-S", f"-{lines}", "-t", target]).splitlines()

    def send_keys(self, target: str, text: str, enter: bool = True) -> None:
        commands = [[self.binary, "send-keys", "-t", target, text]]
        if enter:
            commands.append([self.binary, "send-keys", "-t", target, "Enter"])
        for command in commands:
            self._run(command)
