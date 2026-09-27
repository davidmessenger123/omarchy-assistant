#!/usr/bin/env python3
"""Effective settings for the Omarchy Assistant.

Precedence is environment variable, then ~/.config/omarchy-assistant/config.json,
then the built-in default. The desktop app and the OpenCode tools both read this
so there is a single place to change behaviour.

Usage:
  assistant_config.py --format json          effective settings as one JSON object
  assistant_config.py --format env           KEY=VALUE lines for debugging
  assistant_config.py keys                   known keys with defaults and env names
  assistant_config.py get KEY                one effective value
  assistant_config.py set KEY VALUE          persist a value to the config file
  assistant_config.py path                   config file path
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# key: (default, env var, type, help)
SETTINGS: dict[str, tuple[object, str, type, str]] = {
    "model": ("opencode/space-bunny-free", "ASSISTANT_MODEL", str, "Chat model used for every turn"),
    "model_escalate": ("", "ASSISTANT_MODEL_ESCALATE", str, "Optional stronger model for the escalate button"),
    "image_backend": ("gemini", "ASSISTANT_IMAGE_BACKEND", str, "Default image backend: gemini, local, or auto"),
    "image_model_gemini": ("gemini-3-pro-image", "ASSISTANT_IMAGE_MODEL", str, "Gemini image model id"),
    "image_model_local": ("stabilityai/sdxl-turbo", "ASSISTANT_IMAGE_LOCAL_MODEL", str, "Diffusers model for the local backend"),
    "image_dir": ("", "ASSISTANT_IMAGE_DIR", str, "Where images are saved; empty means ~/Pictures/omarchy-assistant"),
    "image_hourly_limit": (10, "ASSISTANT_IMAGE_HOURLY_LIMIT", int, "Maximum images per hour across backends"),
    "image_daily_limit": (60, "ASSISTANT_IMAGE_DAILY_LIMIT", int, "Maximum images per day across backends"),
    "image_timeout_ms": (300000, "ASSISTANT_IMAGE_TIMEOUT_MS", int, "Gemini request timeout in milliseconds"),
    "image_local_timeout_ms": (3600000, "ASSISTANT_IMAGE_LOCAL_TIMEOUT_MS", int, "Local generation timeout in milliseconds"),
    "confirm_images": (False, "ASSISTANT_CONFIRM_IMAGES", bool, "Ask before generating a paid Gemini image"),
    "clipboard_enabled": (False, "ASSISTANT_CLIPBOARD", bool, "Allow the assistant to read the clipboard"),
    "clipboard_history": (False, "ASSISTANT_CLIPBOARD_HISTORY", bool, "Record clipboard history so earlier copies can be found and pasted again"),
    "max_autonomy_steps": (8, "ASSISTANT_MAX_AUTONOMY_STEPS", int, "Maximum actions in one autonomous task"),
    "max_looks": (3, "ASSISTANT_MAX_LOOKS", int, "Model-requested screenshots allowed per user turn"),
    "screen_monitor": ("auto", "ASSISTANT_SCREEN_MONITOR", str, "Which monitor to capture: auto, cursor, focused, or a name"),
    "reminders_enabled": (True, "ASSISTANT_REMINDERS", bool, "Allow the assistant to set reminders"),
    "notifications": (False, "ASSISTANT_NOTIFICATIONS", bool, "Record notifications so the assistant can say what the user missed"),
    "dictation_command": ("voxtype record toggle", "ASSISTANT_DICTATION_COMMAND", str, "Command run by the microphone button"),
    "keybind_combo": ("SUPER + SHIFT + Q", "ASSISTANT_KEYBIND", str, "Hyprland shortcut that launches the assistant"),
}

CHOICES: dict[str, tuple[str, ...]] = {
    "image_backend": ("gemini", "local", "auto"),
    "screen_monitor": ("auto", "cursor", "focused"),
}


def config_path() -> Path:
    override = os.environ.get("ASSISTANT_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "omarchy-assistant" / "config.json"


def coerce(key: str, raw: object) -> object:
    _default, _env, kind, _help = SETTINGS[key]
    if kind is bool:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if kind is int:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return SETTINGS[key][0]
    return str(raw)


def validate(key: str, value: object) -> object | None:
    """Return an error message when a value is not acceptable, else None."""
    if key not in SETTINGS:
        return f"unknown setting: {key}"
    value = coerce(key, value)
    if key in CHOICES and value not in CHOICES[key]:
        return f"{key} must be one of: {', '.join(CHOICES[key])}"
    if key in ("image_hourly_limit", "image_daily_limit", "max_autonomy_steps", "max_looks", "image_timeout_ms", "image_local_timeout_ms"):
        if int(value) < 0:
            return f"{key} must not be negative"
        if key in ("max_autonomy_steps", "max_looks") and int(value) < 1:
            return f"{key} must be at least 1"
    if key == "model" and not str(value).strip():
        return "model must not be empty"
    return None


def stored(path: Path) -> tuple[dict, str]:
    """Return the file's values plus a warning. A broken file must not be fatal."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, ""
    except OSError as error:
        return {}, f"Could not read {path}: {error}"
    if not text.strip():
        return {}, ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return {}, f"Ignoring {path} because it is not valid JSON: {error}"
    if not isinstance(data, dict):
        return {}, f"Ignoring {path} because it does not contain a JSON object"
    return data, ""


def format_value(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def effective(path: Path) -> tuple[dict, list[str]]:
    values: dict[str, object] = {}
    problems: list[str] = []
    file_values, warning = stored(path)
    if warning:
        problems.append(warning)
    for key, (default, env_name, _kind, _help) in SETTINGS.items():
        if env_name in os.environ:
            values[key] = coerce(key, os.environ[env_name])
        elif key in file_values:
            values[key] = coerce(key, file_values[key])
        else:
            values[key] = default
    for key in file_values:
        if key not in SETTINGS:
            problems.append(f"unknown setting in {path}: {key}")
    return values, problems


def write(path: Path, updates: dict) -> None:
    current, warning = stored(path)
    if warning:
        # Replace a broken file rather than merging into something unreadable.
        current = {}
    current.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="--format")
    parser.add_argument("arguments", nargs="*")
    parser.add_argument("--format", dest="output_format", choices=("json", "env"), default=None)
    arguments = parser.parse_args()
    path = config_path()
    command = arguments.command if arguments.command != "--format" else "--format"

    if command == "path":
        print(path)
        return 0

    if command == "keys":
        width = max(len(key) for key in SETTINGS)
        for key, (default, env_name, _kind, help_text) in SETTINGS.items():
            print(f"{key:<{width}}  env={env_name:<38} default={default!r:<28} {help_text}")
        return 0

    if command == "get":
        if len(arguments.arguments) != 1:
            print("get needs exactly one key", file=sys.stderr)
            return 1
        values, _problems = effective(path)
        key = arguments.arguments[0]
        if key not in SETTINGS:
            print(f"unknown setting: {key}", file=sys.stderr)
            return 1
        print(format_value(values[key]))
        return 0

    if command == "set":
        if len(arguments.arguments) < 2:
            print("set needs a key and a value", file=sys.stderr)
            return 1
        key, raw = arguments.arguments[0], arguments.arguments[1]
        error = validate(key, raw)
        if error:
            print(error, file=sys.stderr)
            return 1
        value = coerce(key, raw)
        write(path, {key: value})
        print(f"{key}={format_value(value)}")
        return 0

    values, problems = effective(path)
    if command == "--format":
        output_format = arguments.output_format or (arguments.arguments[0] if arguments.arguments else "json")
    else:
        print(f"unknown command: {command}", file=sys.stderr)
        return 1
    if output_format == "json":
        print(json.dumps(values))
    else:
        for key, value in values.items():
            print(f"{key}={format_value(value)}")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
