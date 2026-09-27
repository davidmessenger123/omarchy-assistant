#!/usr/bin/env python3
"""A small, bounded clipboard history for the Omarchy Assistant.

Off by default, because the clipboard is where password managers put secrets. When
the user turns it on, a detached watcher records what they copy so they can ask for
it again later. The store is mode 0600, capped in both size and age, and never
leaves the machine.

Usage:
  assistant_clipboard_history.py start     begin recording in the background
  assistant_clipboard_history.py stop      stop recording
  assistant_clipboard_history.py status    is it recording, and how much is stored
  assistant_clipboard_history.py list      recent entries, newest first
  assistant_clipboard_history.py get N     one entry by number
  assistant_clipboard_history.py clear     forget everything
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

MAX_ENTRIES = 200
MAX_CHARS = 20000
MAX_AGE_SECONDS = 24 * 60 * 60
WATCH_INTERVAL = 2
WL_PASTE = "/usr/bin/wl-paste"


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "omarchy-assistant"


def history_path() -> Path:
    return state_dir() / "clipboard-history.json"


def pid_path() -> Path:
    return state_dir() / "clipboard-history.pid"


def load() -> list[dict]:
    try:
        entries = json.loads(history_path().read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def save(entries: list[dict]) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries))
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def prune(entries: list[dict]) -> list[dict]:
    """Drop expired entries, newest first, and keep the store within its cap.

    Sorting here rather than trusting the write order means the numbers the model
    quotes always mean the same entry, whatever wrote the file.
    """
    now = time.time()
    kept = [entry for entry in entries if isinstance(entry.get("at"), (int, float)) and now - entry["at"] < MAX_AGE_SECONDS]
    # Two copies in the same second share a timestamp, so fall back to position:
    # the watcher inserts at the front, which means a lower index is the newer one.
    ordered = sorted(enumerate(kept), key=lambda pair: (pair[1].get("at") or 0, -pair[0]), reverse=True)
    return [entry for _, entry in ordered][:MAX_ENTRIES]


def running_pid() -> int:
    try:
        pid = int(pid_path().read_text().strip())
    except (OSError, ValueError):
        return 0
    if pid <= 0:
        return 0
    try:
        os.kill(pid, 0)
    except OSError:
        return 0
    return pid


def current_text() -> str:
    """Read the clipboard if it holds text, otherwise return nothing."""
    try:
        listed = subprocess.run([WL_PASTE, "--list-types"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if listed.returncode != 0:
        return ""
    types = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not types or not any(value.startswith("text/") for value in types):
        return ""
    try:
        pasted = subprocess.run([WL_PASTE, "--no-newline"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if pasted.returncode != 0:
        return ""
    return pasted.stdout or ""


def record() -> None:
    text = current_text()
    if not text.strip():
        return
    truncated = len(text) > MAX_CHARS
    text = text[:MAX_CHARS]
    entries = prune(load())
    if entries and entries[0].get("text") == text:
        return
    entries.insert(0, {"at": int(time.time()), "chars": len(text), "truncated": truncated, "text": text})
    save(prune(entries))


def watch() -> int:
    """Record every clipboard change until the process is stopped."""
    pid_path().write_text(str(os.getpid()))
    os.chmod(pid_path(), 0o600)
    try:
        while True:
            # Discard the payload: it is re-read deliberately, so images and huge
            # selections never pass through this process.
            watcher = subprocess.Popen([WL_PASTE, "--watch"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            watcher.wait()
            time.sleep(WATCH_INTERVAL)
            record()
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            if pid_path().read_text().strip() == str(os.getpid()):
                pid_path().unlink()
        except OSError:
            pass


def describe(entry: dict, number: int) -> dict:
    text = str(entry.get("text") or "")
    preview = " ".join(text.split())[:80]
    return {
        "number": number,
        "age_minutes": max(0, (int(time.time()) - int(entry.get("at") or 0)) // 60),
        "chars": int(entry.get("chars") or len(text)),
        "truncated": entry.get("truncated") is True,
        "preview": preview
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded clipboard history for the assistant.")
    parser.add_argument("command", choices=["start", "stop", "status", "list", "get", "clear"])
    parser.add_argument("number", nargs="?", type=int, default=0)
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    if args.command == "start":
        existing = running_pid()
        if existing:
            print(json.dumps({"ok": True, "already_running": True, "pid": existing}))
            return 0
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "watch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(20):
            time.sleep(0.1)
            pid = running_pid()
            if pid:
                print(json.dumps({"ok": True, "started": True, "pid": pid}))
                return 0
        print(json.dumps({"ok": False, "error": "the watcher did not start"}))
        return 1

    if args.command == "stop":
        pid = running_pid()
        if pid:
            try:
                os.kill(pid, 15)
            except OSError as error:
                print(json.dumps({"ok": False, "error": f"could not stop it: {error}"}))
                return 1
        try:
            pid_path().unlink()
        except OSError:
            pass
        print(json.dumps({"ok": True, "stopped": bool(pid)}))
        return 0

    if args.command == "status":
        entries = prune(load())
        print(json.dumps({"ok": True, "recording": bool(running_pid()), "entries": len(entries), "max_entries": MAX_ENTRIES, "max_age_hours": MAX_AGE_SECONDS // 3600}))
        return 0

    if args.command == "clear":
        save([])
        print(json.dumps({"ok": True, "cleared": True}))
        return 0

    entries = prune(load())
    save(entries)

    if args.command == "get":
        if args.number < 1 or args.number > len(entries):
            print(json.dumps({"ok": False, "error": f"there are {len(entries)} entries; pick a number between 1 and {max(len(entries), 1)}"}))
            return 1
        entry = entries[args.number - 1]
        print(json.dumps({"ok": True, "number": args.number, "text": str(entry.get("text") or ""), "chars": int(entry.get("chars") or 0), "truncated": entry.get("truncated") is True}))
        return 0

    listed = [describe(entry, index + 1) for index, entry in enumerate(entries)]
    if args.query:
        needle = args.query.lower()
        listed = [item for item in listed if needle in item["preview"].lower()]
    limit = max(1, min(int(args.limit or 20), MAX_ENTRIES))
    print(json.dumps({"ok": True, "count": len(listed), "entries": listed[:limit], "recording": bool(running_pid())}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        sys.exit(watch())
    sys.exit(main())
