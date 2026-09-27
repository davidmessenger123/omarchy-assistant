#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec quickshell --no-duplicate --path "$script_dir/shell.qml"
