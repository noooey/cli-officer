from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from getpass import getpass
from pathlib import Path

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-5-mini",
    "anthropic": "claude-3-5-sonnet-latest",
}

CODING_AGENTS: tuple[str, str] = ("claude-code", "codex")


@dataclass(slots=True)
class ProviderConfig:
    officer_provider: str
    officer_model: str
    officer_api_key: str
    coding_agent: str


def get_config_path() -> Path:
    override = os.environ.get("CLI_OFFICER_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "cli-officer" / "config.json"


def load_config() -> ProviderConfig | None:
    path = get_config_path()
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    officer_provider = payload.get("officer_provider", payload.get("supervisor_provider"))
    officer_model = payload.get("officer_model", payload.get("supervisor_model"))
    officer_api_key = payload.get("officer_api_key", payload.get("supervisor_api_key"))
    return ProviderConfig(
        officer_provider=officer_provider,
        officer_model=officer_model,
        officer_api_key=officer_api_key,
        coding_agent=payload["coding_agent"],
    )


def save_config(config: ProviderConfig) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def ensure_config() -> ProviderConfig:
    existing = load_config()
    if existing:
        return existing
    config = run_first_time_setup()
    save_config(config)
    return config


def run_first_time_setup() -> ProviderConfig:
    print("cli-officer first-time setup")
    print("Choose officer model provider:")
    print("1. OpenAI")
    print("2. Anthropic")
    provider = ""
    while provider not in DEFAULT_MODELS:
        choice = input("Provider [1/2]: ").strip()
        if choice == "1":
            provider = "openai"
        elif choice == "2":
            provider = "anthropic"
    api_key = ""
    while not api_key:
        api_key = getpass(f"{provider} API key: ").strip()
    model = DEFAULT_MODELS[provider]
    print(f"Using fixed officer model: {model}")
    print("Choose coding agent:")
    print("1. claude-code")
    print("2. codex")
    coding_agent = ""
    while coding_agent not in CODING_AGENTS:
        choice = input("Coding agent [1/2]: ").strip()
        if choice == "1":
            coding_agent = "claude-code"
        elif choice == "2":
            coding_agent = "codex"
    print(f"Config will be stored at: {get_config_path()}")
    return ProviderConfig(
        officer_provider=provider,
        officer_model=model,
        officer_api_key=api_key,
        coding_agent=coding_agent,
    )
