from __future__ import annotations

import re

from .models import Interrupt

PROMPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("confirm", re.compile(r"\b(?:y/n|yes/no|continue\?|proceed\?|confirm)\b", re.IGNORECASE)),
    ("retry", re.compile(r"\b(?:retry|try again|rerun)\b", re.IGNORECASE)),
    ("path", re.compile(r"\b(?:enter|provide|input).*(?:path|directory|file)\b", re.IGNORECASE)),
    ("choice", re.compile(r"\b(?:select|choose|pick|option)\b", re.IGNORECASE)),
)


def detect_interrupt(lines: list[str], context_window: int = 8) -> Interrupt | None:
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].strip()
        if not line:
            continue
        for kind, pattern in PROMPT_PATTERNS:
            if pattern.search(line):
                start = max(0, index - context_window)
                return Interrupt(
                    prompt="\n".join(lines[start : index + 1]),
                    prompt_line=line,
                    context=lines[start : index + 1],
                    kind=kind,
                )
        if line.endswith("?") and len(line) < 200:
            start = max(0, index - context_window)
            return Interrupt(
                prompt="\n".join(lines[start : index + 1]),
                prompt_line=line,
                context=lines[start : index + 1],
                kind="question",
            )
    return None
