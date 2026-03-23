# cli-officer

`cli-officer` runs an officer beside a long-running worker pane in tmux.

It watches worker output, detects likely input requests, applies policy, calls an LLM judge, and injects replies back into the worker pane when allowed.

Python requirement: `>=3.10`

## Scope

- Generic design for any worker CLI
- Tested with local fakes, not with a live tmux runtime in this environment
- First run bootstraps an LLM provider config

## Behavior

- Pane 1 runs the coding agent
- Pane 2 runs the officer process
- The officer watches the worker pane, not its own pane
- Officer logs are written to the officer pane stdout
- Idle polling is silent by default; only real events are logged

## Safety defaults

The officer never auto-approves:

- deletion or removal actions
- git push, merge, rebase, force operations
- credential or secret input
- sudo or root-level execution
- sandbox bypass or retry-without-sandbox actions
- deploy or other external side-effect actions

Confidence policy:

- `>= 0.7`: auto
- `0.4 - 0.699`: suggest
- `< 0.4`: block

Reply constraints:

- single line
- no markdown
- no explanation
- terminal-compatible only

You can override hard blocks with `--hard`. That mode allows even dangerous prompts to be decided by the officer, so it is intentionally unsafe.

## Install

Install `tmux` first. Python packaging files do not install system binaries.

Examples:

```bash
./scripts/bootstrap_tmux.sh
```

Or manually:

```bash
sudo apt-get update && sudo apt-get install -y tmux
brew install tmux
```

Install the CLI once from this repository:

```bash
./scripts/install_cli_officer.sh
```

Or manually:

```bash
python3 -m pip install --user --break-system-packages -e /path/to/cli-officer
```

After that, you can run `cli-officer` from any repository.

## First Run

On the first run, `cli-officer` asks you to configure two things with interactive menus:

- Officer model provider
- Coding agent

Officer provider choices:

- OpenAI -> fixed model `gpt-5-mini`
- Anthropic -> fixed model `claude-3-5-sonnet-latest`
- Exit

Coding agent choices:

- `claude-code`
- `codex`
- Exit

In a normal terminal, use `Up` / `Down` and `Enter` to choose. In non-interactive terminals it falls back to numbered input.

Then it asks for the selected officer provider API key.

The config is stored at `~/.config/cli-officer/config.json` with file mode `600`.

You can initialize the config explicitly:

```bash
cli-officer --init
```

Running `cli-officer --init` again reopens setup and overwrites the saved config.

## Run

From the repository you actually want to work on:

```bash
cd /path/to/your-project
```

To attach to an existing worker pane:

```bash
cli-officer --target %1 --once --dry-run
```

To let `cli-officer` create the 2-pane tmux session and launch the selected coding agent itself:

```bash
cli-officer --launch --attach --session-name cli-officer
```

To launch and immediately attach to the created session:

```bash
cli-officer --launch --attach --session-name cli-officer --workdir /path/to/your-project
```

To allow the officer to bypass hard safety blocks:

```bash
cli-officer --launch --attach --hard --session-name cli-officer --workdir /path/to/your-project
```

If you do not use `--attach`, the launch output includes an `attach_command` field you can run manually.

For a continuous loop against an existing worker pane:

```bash
cli-officer --target %1 --interval 1.0
```

## Logs

Example officer pane logs:

```text
[00:42:07] auto-replied kind=confirm risk=low confidence=0.92 reply='yes' reason='Standard confirmation' prompt='Continue? [y/n]'
[00:42:07] blocked-by-policy kind=confirm risk=sandbox-bypass confidence=1.00 reply='-' reason='Matched hard-block pattern: sandbox-bypass' prompt='command failed; retry without sandbox?'
```

## Notes

- `tmux` must be installed in the runtime environment for real pane capture and send-keys.
- In this workspace, `tmux` is not installed, so live integration could not be exercised.
