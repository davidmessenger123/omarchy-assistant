#!/usr/bin/env python3
"""Start push-to-talk dictation for the assistant input.

The desktop app calls this from the microphone button. Dictation is provided by
an external tool (voxtype by default) that types into the focused window, which
is the assistant's input field, so the text lands where the user is typing.

Usage:
  assistant_dictation.py            report whether dictation is available
  assistant_dictation.py start      start the configured dictation command
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_COMMAND = "voxtype record toggle"
INSTALL_HINT = (
    "Dictation needs voxtype, which is not installed. On Omarchy: omarchy pkg add voxtype-bin. "
    "Or set dictation_command in settings to another push-to-talk tool."
)


def command() -> str:
    override = os.environ.get("ASSISTANT_DICTATION_COMMAND")
    if override:
        return override.strip()
    config = Path(os.environ.get("ASSISTANT_CONFIG") or (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "omarchy-assistant" / "config.json"))
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        value = data.get("dictation_command") if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_COMMAND


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    raw = command()
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = []
    binary = parts[0] if parts else ""

    if not binary:
        print(json.dumps({"ok": False, "error": f"The dictation command is not usable: {raw!r}"}))
        return 0
    located = shutil.which(binary)
    if not located:
        if action == "start":
            print(json.dumps({"ok": False, "available": False, "command": raw, "error": INSTALL_HINT}))
        else:
            print(json.dumps({"ok": True, "available": False, "command": raw, "hint": INSTALL_HINT}))
        return 0

    if action == "start":
        # The tool types into whatever has focus, which is the assistant input.
        subprocess.Popen(
            [located, *parts[1:]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(json.dumps({"ok": True, "available": True, "command": raw, "started": True}))
        return 0

    print(json.dumps({"ok": True, "available": True, "command": raw}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
