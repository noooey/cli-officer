from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sys
from urllib import error, parse, request

from .config import ProviderConfig
from .models import Decision, DecisionMode, Interrupt


class Judge:
    def decide(self, interrupt: Interrupt) -> Decision:
        raise NotImplementedError


@dataclass(slots=True)
class HeuristicJudge(Judge):
    default_path: str = "."

    def decide(self, interrupt: Interrupt) -> Decision:
        prompt = interrupt.prompt_line.lower()
        if interrupt.kind == "confirm":
            return Decision(True, "low", DecisionMode.AUTO, "yes", 0.92, "Standard confirmation")
        if interrupt.kind == "approval":
            return Decision(True, "low", DecisionMode.AUTO, "yes", 0.86, "Natural-language approval prompt")
        if interrupt.kind == "retry":
            return Decision(True, "low", DecisionMode.AUTO, "retry", 0.88, "Retry prompt")
        if interrupt.kind == "path":
            return Decision(True, "medium", DecisionMode.AUTO, self.default_path, 0.78, "Path request")
        if interrupt.kind == "choice":
            reply = self._pick_choice(prompt, interrupt.context)
            return Decision(True, "medium", DecisionMode.AUTO if reply else DecisionMode.SUGGEST, reply, 0.74 if reply else 0.55, "Choice prompt")
        if interrupt.kind == "stalled":
            return Decision(False, "unknown", DecisionMode.BLOCK, "", 0.0, "No explicit reply request detected", False)
        return Decision(True, "unknown", DecisionMode.SUGGEST, "", 0.35, "Unknown prompt")

    @staticmethod
    def _pick_choice(prompt: str, context: list[str]) -> str:
        if "[y/n]" in prompt or "yes/no" in prompt:
            return "yes"
        if "1" in prompt:
            return "1"
        for line in context:
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                candidate = re.sub(r"^[-*]\s+", "", stripped).strip()
                if candidate:
                    return candidate
        return ""


SYSTEM_PROMPT = """You are a CLI officer for a coding agent.
Return JSON only with keys:
interrupt_detected, risk_level, mode, reply, confidence, rationale, needs_reply

Rules:
- mode must be one of: auto, suggest, block
- reply must be a single line terminal-safe input with no explanation
- do not invent secrets, paths, or unknown values
- if the worker is not clearly waiting for user input, set needs_reply to false
- prefer suggest or block if uncertain
"""

OPENAI_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "interrupt_detected": {"type": "boolean"},
        "risk_level": {"type": "string"},
        "mode": {"type": "string", "enum": ["auto", "suggest", "block"]},
        "reply": {"type": "string"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "needs_reply": {"type": "boolean"},
    },
    "required": ["interrupt_detected", "risk_level", "mode", "reply", "confidence", "rationale", "needs_reply"],
}


@dataclass(slots=True)
class APIDecisionJudge(Judge):
    config: ProviderConfig
    fallback: Judge

    def decide(self, interrupt: Interrupt) -> Decision:
        try:
            payload = self._call_api(interrupt)
            return self._parse_decision(payload)
        except (OSError, ValueError, KeyError, error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
            print(f"[officer] LLM API unavailable: {exc}; falling back to heuristic", file=sys.stderr, flush=True)
            return self.fallback.decide(interrupt)

    def _call_api(self, interrupt: Interrupt) -> dict:
        if self.config.officer_provider == "openai":
            return self._call_openai(interrupt)
        if self.config.officer_provider == "anthropic":
            return self._call_anthropic(interrupt)
        raise ValueError(f"Unsupported provider: {self.config.officer_provider}")

    def _call_openai(self, interrupt: Interrupt) -> dict:
        body = {
            "model": self.config.officer_model,
            "instructions": SYSTEM_PROMPT,
            "input": self._build_user_prompt(interrupt),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "officer_decision",
                    "strict": True,
                    "schema": OPENAI_DECISION_SCHEMA,
                }
            },
        }
        req = request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.officer_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = _extract_openai_output_text(payload)
        if not text:
            raise ValueError(f"OpenAI response missing output_text: {json.dumps(payload)[:500]}")
        return json.loads(text)

    def _call_anthropic(self, interrupt: Interrupt) -> dict:
        body = {
            "model": self.config.officer_model,
            "max_tokens": 300,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": self._build_user_prompt(interrupt)},
            ],
        }
        req = request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-api-key": f"{self.config.officer_api_key}",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        parts = payload.get("content", [])
        text = "".join(part.get("text", "") for part in parts if part.get("type") == "text").strip()
        if not text:
            raise ValueError(f"Anthropic response missing text content: {json.dumps(payload)[:500]}")
        return json.loads(text)

    @staticmethod
    def _clean_line(line: str) -> str:
        return re.sub(r"\s+", " ", line.replace("│", " ").replace("|", " ")).strip()

    @classmethod
    def _build_user_prompt(cls, interrupt: Interrupt) -> str:
        return json.dumps(
            {
                "kind": interrupt.kind,
                "prompt_line": cls._clean_line(interrupt.prompt_line),
                "context": [cls._clean_line(l) for l in interrupt.context if l.strip()],
            },
            ensure_ascii=True,
        )

    @staticmethod
    def _parse_decision(payload: dict) -> Decision:
        mode = DecisionMode(payload["mode"])
        confidence = float(payload["confidence"])
        return Decision(
            interrupt_detected=bool(payload.get("interrupt_detected", True)),
            risk_level=str(payload.get("risk_level", "unknown")),
            mode=mode,
            reply=str(payload.get("reply", "")),
            confidence=confidence,
            rationale=str(payload.get("rationale", "")),
            needs_reply=bool(payload.get("needs_reply", True)),
        )


def build_judge(config: ProviderConfig | None) -> Judge:
    fallback = HeuristicJudge(default_path=".")
    if config is None:
        return fallback
    return APIDecisionJudge(config=config, fallback=fallback)


def validate_provider_config(config: ProviderConfig) -> None:
    if config.officer_provider == "openai":
        _validate_openai(config)
        return
    if config.officer_provider == "anthropic":
        _validate_anthropic(config)
        return
    raise ValueError(f"Unsupported provider: {config.officer_provider}")


def _validate_openai(config: ProviderConfig) -> None:
    req = request.Request(
        f"https://api.openai.com/v1/models/{parse.quote(config.officer_model, safe='')}",
        headers={
            "Authorization": f"Bearer {config.officer_api_key}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            if response.status >= 400:
                raise ValueError(f"OpenAI validation failed with status {response.status}")
    except error.HTTPError as exc:
        raise ValueError(_format_http_error("OpenAI", exc)) from exc


def _validate_anthropic(config: ProviderConfig) -> None:
    body = {
        "model": config.officer_model,
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply with OK."}],
    }
    req = request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": config.officer_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            if response.status >= 400:
                raise ValueError(f"Anthropic validation failed with status {response.status}")
    except error.HTTPError as exc:
        raise ValueError(_format_http_error("Anthropic", exc)) from exc


def _format_http_error(provider: str, exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    if body:
        body = body[:300]
        return f"{provider} validation failed with status {exc.code}: {body}"
    return f"{provider} validation failed with status {exc.code}"


def _extract_openai_output_text(payload: dict) -> str:
    output_text = str(payload.get("output_text", "")).strip()
    if output_text:
        return output_text
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = str(content.get("text", "")).strip()
                if text:
                    return text
    return ""
