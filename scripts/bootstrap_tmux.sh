#!/usr/bin/env bash
set -euo pipefail

if command -v tmux >/dev/null 2>&1; then
  echo "tmux is already installed"
  exit 0
fi

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y tmux
  exit 0
fi

if command -v brew >/dev/null 2>&1; then
  brew install tmux
  exit 0
fi

if command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y tmux
  exit 0
fi

if command -v yum >/dev/null 2>&1; then
  sudo yum install -y tmux
  exit 0
fi

echo "Unsupported package manager. Install tmux manually and re-run."
exit 1
