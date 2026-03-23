#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m pip install -e "$REPO_ROOT"
echo "Installed cli-officer from $REPO_ROOT"
echo "You can now run: cli-officer --init"
