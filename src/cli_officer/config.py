from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from getpass import getpass
from pathlib import Path
import termios
import tty

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


def ensure_config(force_reconfigure: bool = False) -> ProviderConfig:
    if not force_reconfigure:
        existing = load_config()
        if existing:
            return existing
    config = run_first_time_setup()
    save_config(config)
    return config


def run_first_time_setup() -> ProviderConfig:
    print("cli-officer first-time setup")
    provider = choose_from_menu(
        title="Choose officer model provider:",
        choices={
            "1": "openai",
            "2": "anthropic",
            "3": "exit",
        },
        labels={
            "1": "OpenAI",
            "2": "Anthropic",
            "3": "Exit",
        },
        prompt="Provider [1/2/3]: ",
    )
    if provider == "exit":
        raise SystemExit(0)
    model = DEFAULT_MODELS[provider]
    print(f"Using fixed officer model: {model}")
    api_key = prompt_valid_api_key(provider=provider, model=model)
    coding_agent = choose_from_menu(
        title="Choose coding agent:",
        choices={
            "1": "claude-code",
            "2": "codex",
            "3": "exit",
        },
        labels={
            "1": "claude-code",
            "2": "codex",
            "3": "Exit",
        },
        prompt="Coding agent [1/2/3]: ",
    )
    if coding_agent == "exit":
        raise SystemExit(0)
    print(f"Config will be stored at: {get_config_path()}")
    return ProviderConfig(
        officer_provider=provider,
        officer_model=model,
        officer_api_key=api_key,
        coding_agent=coding_agent,
    )


def prompt_valid_api_key(provider: str, model: str) -> str:
    from .llm import validate_provider_config

    while True:
        api_key = getpass(f"{provider} API key: ").strip()
        if not api_key:
            print("API key cannot be empty.", file=sys.stderr)
            continue
        try:
            validate_provider_config(
                ProviderConfig(
                    officer_provider=provider,
                    officer_model=model,
                    officer_api_key=api_key,
                    coding_agent="codex",
                )
            )
            print("API key validated successfully.")
            return api_key
        except Exception as exc:
            print(f"API key validation failed: {exc}", file=sys.stderr)
            print("Enter a different key or press Ctrl+C to exit.", file=sys.stderr)


def choose_from_menu(title: str, choices: dict[str, str], labels: dict[str, str], prompt: str) -> str:
    ordered_keys = [key for key in ("1", "2", "3") if key in labels]
    if sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM"):
        return _choose_with_inline_menu(title=title, choices=choices, labels=labels, ordered_keys=ordered_keys)
    return _choose_with_text_prompt(title=title, choices=choices, labels=labels, ordered_keys=ordered_keys, prompt=prompt)


def _choose_with_text_prompt(
    title: str,
    choices: dict[str, str],
    labels: dict[str, str],
    ordered_keys: list[str],
    prompt: str,
) -> str:
    print(title)
    for key in ordered_keys:
        print(f"{key}. {labels[key]}")
    while True:
        selection = input(prompt).strip()
        if selection in choices:
            return choices[selection]
        print("Invalid selection. Choose one of the listed numbers.", file=sys.stderr)


def _choose_with_inline_menu(title: str, choices: dict[str, str], labels: dict[str, str], ordered_keys: list[str]) -> str:
    options = [(key, labels[key], choices[key]) for key in ordered_keys]
    selected = 0
    printed_lines = 0
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            if printed_lines:
                sys.stdout.write(f"\x1b[{printed_lines}F")
            lines = [title, "Use Up/Down and Enter."]
            for index, (_, label, value) in enumerate(options):
                prefix = "> " if index == selected else "  "
                lines.append(f"{prefix}{label}")
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            printed_lines = len(lines)

            key = sys.stdin.read(1)
            if key == "\x1b":
                sequence = key + sys.stdin.read(2)
                if sequence == "\x1b[A":
                    selected = (selected - 1) % len(options)
                elif sequence == "\x1b[B":
                    selected = (selected + 1) % len(options)
            elif key in ("k",):
                selected = (selected - 1) % len(options)
            elif key in ("j",):
                selected = (selected + 1) % len(options)
            elif key in ("\r", "\n"):
                sys.stdout.write(f"\x1b[{printed_lines}F")
                sys.stdout.write("\x1b[J")
                sys.stdout.flush()
                return options[selected][2]
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
