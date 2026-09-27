#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

usage() {
    cat <<'EOF'
Usage: install.sh [options]

  --with-image-model     Also install the local image backend (about 13 GB:
                         PyTorch, diffusers, and the SDXL-Turbo weights)
  --image-model-args "…" Pass extra options to bin/assistant-config setup-image-models
  --keybind-combo "…"    Hyprland shortcut to bind (default: SUPER + SHIFT + Q)
  --force-keybind        Replace a different binding that uses the same combo
  --no-keybind           Do not touch the Hyprland config; print the line instead
  --yes                  Do not ask for confirmation before the model download
  -h, --help             Show this help

Without options, install.sh only installs the OpenCode tool dependencies, which
is fast. Image models are never downloaded automatically, and the in-app Update
button never downloads them either.

The Hyprland keybinding is added to ~/.config/hypr/bindings.lua inside a marked
block, with a timestamped backup, and only when nothing else already uses that
combination. Pass --no-keybind to skip it.
EOF
}

with_image_model=0
assume_yes=0
image_model_args=""
add_keybind=1
force_keybind=0
keybind_combo="SUPER + SHIFT + Q"
while (($# > 0)); do
    case "$1" in
        --with-image-model)
            with_image_model=1
            ;;
        --image-model-args)
            shift
            image_model_args="${1:-}"
            if [[ -z "$image_model_args" ]]; then
                printf -- '--image-model-args needs a value.\n' >&2
                exit 1
            fi
            ;;
        --keybind-combo)
            shift
            keybind_combo="${1:-}"
            if [[ -z "$keybind_combo" ]]; then
                printf -- '--keybind-combo needs a value.\n' >&2
                exit 1
            fi
            ;;
        --force-keybind)
            force_keybind=1
            ;;
        --no-keybind)
            add_keybind=0
            ;;
        --yes | -y)
            assume_yes=1
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n\n' "$1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

required=(quickshell opencode python3 node npm git hyprctl wtype uwsm-app xdg-open)
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
if ! python3 -c 'import venv' >/dev/null 2>&1; then
    printf 'Warning: python3 cannot create virtual environments.\n' >&2
    printf 'Install the venv module (Arch: pacman -S python; Debian/Ubuntu: python3-venv) before setting up the image model.\n' >&2
fi
chmod +x "$script_dir/run.sh" "$script_dir/screen_click.py" "$script_dir/assistant_memory.py" "$script_dir/assistant_update.py" "$script_dir/assistant_image_local.py" "$script_dir/bin/assistant-config" "$script_dir/bin/install-keybind" "$script_dir/bin/screen_capture" "$script_dir/bin/screen_capture_secure" "$script_dir/assistant_windows.py" "$script_dir/assistant_clipboard_history.py" "$script_dir/assistant_notifications.py" "$script_dir/assistant_recipes.py"
npm install --prefix "$script_dir/.opencode"
printf 'Installed OpenCode tool dependencies.\n'

if ((with_image_model)); then
    printf '\nLocal image model setup\n'
    "$script_dir/bin/assistant-config" image-setup-plan
    if ((assume_yes == 0)); then
        if [[ -t 0 ]]; then
            printf '\nThis downloads several GB and can take a long time. Continue? [y/N] ' >&2
        else
            printf '\nNot a terminal, so continuing without confirmation (pass --yes to silence this).\n' >&2
            assume_yes=1
        fi
    fi
    if ((assume_yes == 0)); then
        IFS= read -r reply || reply=""
        case "${reply,,}" in
            y | yes) ;;
            *)
                printf 'Skipping the local image model. Run bin/assistant-config setup-image-models later.\n'
                with_image_model=0
                ;;
        esac
    fi
    if ((with_image_model)); then
        # Word splitting is intended here so extra options reach the setup command.
        # shellcheck disable=SC2086
        "$script_dir/bin/assistant-config" setup-image-models ${image_model_args:+"$image_model_args"}
        printf '\nClick the Img button in the assistant to choose the local backend.\n'
    fi
fi

printf '\nHyprland keybinding\n'
if ((add_keybind)); then
    keybind_args=(--combo "$keybind_combo")
    if ((force_keybind)); then
        keybind_args+=(--force)
    fi
    if ! "$script_dir/bin/install-keybind" "${keybind_args[@]}"; then
        printf 'The keybinding could not be installed automatically; add it by hand:\n' >&2
        printf '  o.bind("%s", "Omarchy Assistant", { launch = "%s/run.sh" })\n' "$keybind_combo" "$script_dir" >&2
    fi
else
    printf 'Skipped. Add this to ~/.config/hypr/bindings.lua:\n'
    printf '  o.bind("%s", "Omarchy Assistant", { launch = "%s/run.sh" })\n' "$keybind_combo" "$script_dir"
fi

printf 'Launch with: %s/run.sh\n' "$script_dir"
if [[ ! -e /dev/uinput ]]; then
    printf 'Warning: /dev/uinput is unavailable; autonomous clicking will not work until access is granted.\n' >&2
fi
