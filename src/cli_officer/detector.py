from __future__ import annotations

import re

from .models import Interrupt

LINE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("confirm", re.compile(r"\b(?:y/n|yes/no|continue\?|proceed\?|confirm)\b", re.IGNORECASE)),
    ("retry", re.compile(r"\b(?:retry|try again|rerun)\b", re.IGNORECASE)),
    ("path", re.compile(r"\b(?:enter|provide|input).*(?:path|directory|file)\b", re.IGNORECASE)),
)

CHOICE_PATTERN = re.compile(r"\b(?:select|choose|pick|option|one of)\b|중 하나", re.IGNORECASE)
APPROVAL_PATTERN = re.compile(
    r"(?:\bif you want\b|\bwould you like me to\b|\bshall i\b|\bwant me to\b|\bi can\b|원하면|원하시면|원하신다면)",
    re.IGNORECASE,
)
QUESTION_PATTERN = re.compile(r"\?")
KOREAN_OFFER_ENDING = re.compile(r"(?:드릴게요|드리겠습니다|하겠습니다|해드리겠습니다|진행할까요)$")


def detect_interrupt(lines: list[str], context_window: int = 8) -> Interrupt | None:
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].strip()
        if not line:
            continue
        for kind, pattern in LINE_PATTERNS:
            if pattern.search(line):
                start = max(0, index - context_window)
                return Interrupt(
                    prompt="\n".join(lines[start : index + 1]),
                    prompt_line=line,
                    context=lines[start : index + 1],
                    kind=kind,
                )
        if CHOICE_PATTERN.search(line):
            start = max(0, index - context_window)
            return Interrupt(
                prompt="\n".join(lines[start : index + 1]),
                prompt_line=line,
                context=lines[start : index + 1],
                kind="choice",
            )
        if APPROVAL_PATTERN.search(line):
            start = max(0, index - context_window)
            return Interrupt(
                prompt="\n".join(lines[start : index + 1]),
                prompt_line=line,
                context=lines[start : index + 1],
                kind="approval",
            )
        if re.match(r"^[›>]\s+\d+[.)]\s+", line):
            start = max(0, index - context_window)
            return Interrupt(
                prompt="\n".join(lines[start : index + 1]),
                prompt_line=line,
                context=lines[start : index + 1],
                kind="confirm",
            )
        if _looks_like_bulleted_choice(lines, index):
            start = max(0, index - context_window)
            return Interrupt(
                prompt="\n".join(lines[start : index + 1]),
                prompt_line=line,
                context=lines[start : index + 1],
                kind="choice",
            )
        line_no_border = re.sub(r"[\s│|]+$", "", line)
        if line_no_border.endswith("?") and len(line) < 200:
            start = max(0, index - context_window)
            return Interrupt(
                prompt="\n".join(lines[start : index + 1]),
                prompt_line=line,
                context=lines[start : index + 1],
                kind="question",
            )
    return _detect_from_recent_context(lines, context_window=context_window)


def _detect_from_recent_context(lines: list[str], context_window: int = 8) -> Interrupt | None:
    non_empty_indexes = [index for index, line in enumerate(lines) if line.strip()]
    if not non_empty_indexes:
        return None
    last_index = non_empty_indexes[-1]
    start = max(0, last_index - context_window + 1)
    context = lines[start : last_index + 1]
    kind = _classify_candidate_block(context)
    if kind is not None:
        return Interrupt(
            prompt="\n".join(context),
            prompt_line=_last_semantic_line(context),
            context=context,
            kind=kind,
        )
    if _looks_like_reply_request_context(context):
        return Interrupt(
            prompt="\n".join(context),
            prompt_line=_last_semantic_line(context),
            context=context,
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
    kind = _classify_candidate_block(context)
    if kind is None and not _looks_like_reply_request_context(context):
        return None
    prompt_line = next((line.strip() for line in reversed(context) if line.strip()), "")
    if not prompt_line:
        return None
    return Interrupt(
        prompt="\n".join(context),
        prompt_line=prompt_line,
        context=context,
        kind=kind or "stalled",
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


def _normalize_joined_text(lines: list[str]) -> str:
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        stripped = stripped.replace("│", " ")
        stripped = re.sub(r"\s+", " ", stripped)
        cleaned.append(stripped)
    return " ".join(cleaned).strip()


def _classify_candidate_block(context: list[str]) -> str | None:
    combined = _normalize_joined_text(context)
    if not combined:
        return None
    if CHOICE_PATTERN.search(combined):
        return "choice"
    if APPROVAL_PATTERN.search(combined):
        return "approval"
    if QUESTION_PATTERN.search(combined):
        return "question"
    if KOREAN_OFFER_ENDING.search(combined):
        return "approval"
    for kind, pattern in LINE_PATTERNS:
        if pattern.search(combined):
            return kind
    return None


def _last_semantic_line(context: list[str]) -> str:
    for line in reversed(context):
        stripped = line.strip()
        if stripped:
            return stripped.replace("│", " ").strip()
    return ""


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
