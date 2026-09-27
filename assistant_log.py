#!/usr/bin/env python3
"""Local action log and single-use image approvals for the Omarchy Assistant.

The log is append-only, newest entries are returned first, and the file is mode
0600. Typed text is never stored: the desktop app logs what an action targeted,
not what it typed.

Usage:
  assistant_log.py append '<json>'     add one entry: kind, summary, detail
  assistant_log.py list [--limit N]    print entries newest first as JSON
  assistant_log.py clear               remove the log
  assistant_log.py set-approval '<json>'  store a one-time image approval
  assistant_log.py get-approval        print the stored approval, if any
  assistant_log.py clear-approval      discard the stored approval
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

MAX_LINES = 2000
KEEP_LINES = 1200
MAX_SUMMARY = 200
MAX_DETAIL = 400
KINDS = (
    "click",
    "type",
    "app",
    "image",
    "file",
    "screen",
    "web",
    "memory",
    "settings",
    "update",
    "task",
    "reminder",
    "clipboard",
    "look",
    "note",
)


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "omarchy-assistant"


def log_path() -> Path:
    return state_dir() / "action-log.jsonl"


def approval_path() -> Path:
    return state_dir() / "image-approval.json"


def clean(value: object, limit: int) -> str:
    """Flatten a value to a single safe line of bounded length."""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, separators=(",", ":"))
    text = "".join(character for character in str(value) if character.isprintable())
    text = " ".join(text.split())
    return text[:limit]


def append_entry(payload: dict) -> dict:
    kind = clean(payload.get("kind") or "note", 32)
    if kind not in KINDS:
        kind = "note"
    now = time.time()
    entry = {
        "at": int(now * 1000),
        "time": datetime.fromtimestamp(now).strftime("%H:%M:%S"),
        "kind": kind,
        "summary": clean(payload.get("summary") or "", MAX_SUMMARY),
        "detail": clean(payload.get("detail") or "", MAX_DETAIL),
    }
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = read_lines(path)
    lines.append(json.dumps(entry))
    if len(lines) > MAX_LINES:
        lines = lines[-KEEP_LINES:]
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
    return entry


def read_lines(path: Path) -> list[str]:
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return []
    except OSError as error:
        print(f"Could not read {path}: {error}", file=sys.stderr)
        return []


def list_entries(limit: int) -> list[dict]:
    entries: list[dict] = []
    for line in reversed(read_lines(log_path())):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def set_approval(payload: dict) -> dict:
    request = {
        "prompt": clean(payload.get("prompt") or "", 4000),
        "backend": clean(payload.get("backend") or "gemini", 20),
        "aspect_ratio": clean(payload.get("aspect_ratio") or "1:1", 10),
        "resolution": clean(payload.get("resolution") or "1K", 10),
        "approved_at": int(time.time()),
    }
    if not request["prompt"]:
        print("An approval needs a prompt", file=sys.stderr)
        raise SystemExit(1)
    path = approval_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(request) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
    return request


def get_approval() -> dict:
    try:
        data = json.loads(approval_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("append", "list", "clear", "set-approval", "get-approval", "clear-approval", "path"))
    parser.add_argument("payload", nargs="?")
    parser.add_argument("--limit", type=int, default=60)
    arguments = parser.parse_args()

    if arguments.command == "path":
        print(log_path())
        return 0

    if arguments.command == "append":
        if not arguments.payload:
            print("append needs a JSON payload", file=sys.stderr)
            return 1
        try:
            payload = json.loads(arguments.payload)
        except json.JSONDecodeError as error:
            print(f"payload was not valid JSON: {error}", file=sys.stderr)
            return 1
        if not isinstance(payload, dict):
            print("payload must be a JSON object", file=sys.stderr)
            return 1
        print(json.dumps(append_entry(payload)))
        return 0

    if arguments.command == "list":
        print(json.dumps(list_entries(max(1, min(arguments.limit, MAX_LINES)))))
        return 0

    if arguments.command == "clear":
        path = log_path()
        if path.exists():
            path.unlink()
        print("cleared")
        return 0

    if arguments.command == "set-approval":
        if not arguments.payload:
            print("set-approval needs a JSON payload", file=sys.stderr)
            return 1
        try:
            payload = json.loads(arguments.payload)
        except json.JSONDecodeError as error:
            print(f"payload was not valid JSON: {error}", file=sys.stderr)
            return 1
        if not isinstance(payload, dict):
            print("payload must be a JSON object", file=sys.stderr)
            return 1
        print(json.dumps(set_approval(payload)))
        return 0

    if arguments.command == "get-approval":
        print(json.dumps(get_approval()))
        return 0

    if arguments.command == "clear-approval":
        path = approval_path()
        if path.exists():
            path.unlink()
        print("cleared")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
