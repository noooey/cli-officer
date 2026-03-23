from __future__ import annotations

from dataclasses import dataclass
import json
from urllib import error, request

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
        if interrupt.kind == "retry":
            return Decision(True, "low", DecisionMode.AUTO, "retry", 0.88, "Retry prompt")
        if interrupt.kind == "path":
            return Decision(True, "medium", DecisionMode.AUTO, self.default_path, 0.78, "Path request")
        if interrupt.kind == "choice":
            reply = self._pick_choice(prompt)
            return Decision(True, "medium", DecisionMode.SUGGEST, reply, 0.55, "Choice prompt")
        return Decision(True, "unknown", DecisionMode.SUGGEST, "", 0.35, "Unknown prompt")

    @staticmethod
    def _pick_choice(prompt: str) -> str:
        if "[y/n]" in prompt or "yes/no" in prompt:
            return "yes"
        if "1" in prompt:
            return "1"
        return ""


SYSTEM_PROMPT = """You are a CLI officer for a coding agent.
Return JSON only with keys:
interrupt_detected, risk_level, mode, reply, confidence, rationale

Rules:
- mode must be one of: auto, suggest, block
- reply must be a single line terminal-safe input with no explanation
- do not invent secrets, paths, or unknown values
- prefer suggest or block if uncertain
"""


@dataclass(slots=True)
class APIDecisionJudge(Judge):
    config: ProviderConfig
    fallback: Judge

    def decide(self, interrupt: Interrupt) -> Decision:
        try:
            payload = self._call_api(interrupt)
            return self._parse_decision(payload)
        except (OSError, ValueError, KeyError, error.URLError, error.HTTPError, json.JSONDecodeError):
            return Decision(
                interrupt_detected=True,
                risk_level="unknown",
                mode=DecisionMode.SUGGEST,
                reply="",
                confidence=0.0,
                rationale="LLM API unavailable",
            )

    def _call_api(self, interrupt: Interrupt) -> dict:
        if self.config.officer_provider == "openai":
            return self._call_openai(interrupt)
        if self.config.officer_provider == "anthropic":
            return self._call_anthropic(interrupt)
        raise ValueError(f"Unsupported provider: {self.config.officer_provider}")

    def _call_openai(self, interrupt: Interrupt) -> dict:
        body = {
            "model": self.config.officer_model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
                {"role": "user", "content": [{"type": "input_text", "text": self._build_user_prompt(interrupt)}]},
            ],
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
        text = payload.get("output_text", "").strip()
        if not text:
            raise ValueError("OpenAI response missing output_text")
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
            raise ValueError("Anthropic response missing text content")
        return json.loads(text)

    @staticmethod
    def _build_user_prompt(interrupt: Interrupt) -> str:
        return json.dumps(
            {
                "kind": interrupt.kind,
                "prompt_line": interrupt.prompt_line,
                "context": interrupt.context,
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
    body = {
        "model": config.officer_model,
        "input": "Reply with OK.",
        "max_output_tokens": 8,
    }
    req = request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.officer_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        if response.status >= 400:
            raise ValueError(f"OpenAI validation failed with status {response.status}")


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
    with request.urlopen(req, timeout=30) as response:
        if response.status >= 400:
            raise ValueError(f"Anthropic validation failed with status {response.status}")
