# cli-officer

`cli-officer` is a Python MVP for supervising a long-running worker pane in tmux.

It captures pane output, detects likely input requests, runs a judge, applies hard safety policy, and optionally injects replies back into the worker pane.

## Scope

- Generic design for any worker CLI
- Tested with local fakes, not with a live tmux runtime in this environment
- First run bootstraps an LLM provider config

## Safety defaults

The supervisor never auto-approves:

- deletion or removal actions
- git push, merge, rebase, force operations
- credential or secret input
- sudo or root-level execution
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

## Run

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

On the first run, `cli-officer` asks you to configure two things:

- Officer supervisor model provider
- Coding agent

Supervisor provider choices:

- OpenAI -> fixed model `gpt-5-mini`
- Anthropic -> fixed model `claude-3-5-sonnet-latest`

Coding agent choices:

- `claude-code`
- `codex`

Then it asks for the selected supervisor provider API key.

The config is stored at `~/.config/cli-officer/config.json` with file mode `600`.

To attach to an existing worker pane:

```bash
python3 -m cli_officer --target %1 --once --dry-run
```

You can also initialize config explicitly:

```bash
python3 -m cli_officer --init
```

To let `cli-officer` create the 2-pane tmux session and launch the selected coding agent itself:

```bash
python3 -m cli_officer --launch --session-name cli-officer --workdir .
```

To launch and immediately attach to the created session:

```bash
python3 -m cli_officer --launch --attach --session-name cli-officer --workdir .
```

If you do not use `--attach`, the launch output includes an `attach_command` field you can run manually.

For a continuous loop against an existing worker pane:

```bash
python3 -m cli_officer --target %1 --interval 1.0
```

## Notes

- `tmux` must be installed in the runtime environment for real pane capture and send-keys.
- In this workspace, `tmux` is not installed, so live integration could not be exercised.
