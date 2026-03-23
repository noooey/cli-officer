from __future__ import annotations

import re

from .models import Interrupt

PROMPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("confirm", re.compile(r"\b(?:y/n|yes/no|continue\?|proceed\?|confirm)\b", re.IGNORECASE)),
    ("retry", re.compile(r"\b(?:retry|try again|rerun)\b", re.IGNORECASE)),
    ("path", re.compile(r"\b(?:enter|provide|input).*(?:path|directory|file)\b", re.IGNORECASE)),
    ("choice", re.compile(r"\b(?:select|choose|pick|option|one of)\b|중 하나", re.IGNORECASE)),
    (
        "approval",
        re.compile(
            r"(?:\bif you want\b|\bwould you like me to\b|\bshall i\b|\bi can\b.*\bfor you\b|\bwant me to\b|원하면|원하시면|원하신다면|바로 .*해드리겠습니다|바꿔드리겠습니다|정리하겠습니다|진행할까요)",
            re.IGNORECASE,
        ),
    ),
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
        if _looks_like_bulleted_choice(lines, index):
            start = max(0, index - context_window)
            return Interrupt(
                prompt="\n".join(lines[start : index + 1]),
                prompt_line=line,
                context=lines[start : index + 1],
                kind="choice",
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


def extract_stalled_candidate(lines: list[str], context_window: int = 12) -> Interrupt | None:
    non_empty_indexes = [index for index, line in enumerate(lines) if line.strip()]
    if not non_empty_indexes:
        return None
    last_index = non_empty_indexes[-1]
    start = max(0, last_index - context_window + 1)
    context = lines[start : last_index + 1]
    if not _looks_like_reply_request_context(context):
        return None
    prompt_line = next((line.strip() for line in reversed(context) if line.strip()), "")
    if not prompt_line:
        return None
    return Interrupt(
        prompt="\n".join(context),
        prompt_line=prompt_line,
        context=context,
        kind="stalled",
    )


def _looks_like_reply_request_context(context: list[str]) -> bool:
    bullet_count = 0
    numbered_count = 0
    for line in context:
        stripped = line.strip()
        if not stripped:
            continue
        if "?" in stripped:
            return True
        if re.match(r"^\d+[.)]\s+", stripped):
            numbered_count += 1
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullet_count += 1
    return bullet_count >= 2 or numbered_count >= 2


def _looks_like_bulleted_choice(lines: list[str], index: int) -> bool:
    line = lines[index].strip()
    if "중 하나" not in line and "one of" not in line.lower():
        return False
    tail = lines[index : min(len(lines), index + 6)]
    bullet_count = 0
    for candidate in tail:
        stripped = candidate.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullet_count += 1
    return bullet_count >= 2
