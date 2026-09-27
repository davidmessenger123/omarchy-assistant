#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
required=(quickshell opencode python3 node npm hyprctl wtype uwsm-app xdg-open)
missing=()
for command_name in "${required[@]}"; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        missing+=("$command_name")
    fi
done
if ((${#missing[@]} > 0)); then
    printf 'Missing required commands: %s\n' "${missing[*]}" >&2
    printf 'Install the Omarchy/Wayland dependencies first; see README.md.\n' >&2
    exit 1
fi
if [[ ! -x "$script_dir/bin/screen_capture" ]]; then
    printf 'Missing bundled bin/screen_capture helper.\n' >&2
    exit 1
fi
chmod +x "$script_dir/run.sh" "$script_dir/screen_click.py" "$script_dir/assistant_memory.py" "$script_dir/bin/screen_capture_secure"
npm install --prefix "$script_dir/.opencode"
printf 'Installed OpenCode tool dependencies.\n'
printf 'Launch with: %s/run.sh\n' "$script_dir"
printf 'Add this Hyprland binding to ~/.config/hypr/bindings.lua:\n'
printf '  o.bind("SUPER + SHIFT + Q", "Omarchy Assistant", { launch = "%s/run.sh" })\n' "$script_dir"
if [[ ! -e /dev/uinput ]]; then
    printf 'Warning: /dev/uinput is unavailable; autonomous clicking will not work until access is granted.\n' >&2
fi
