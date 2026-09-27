#!/usr/bin/env python3
"""Notification history and catch-up for the Omarchy Assistant.

Omarchy's shell is the notification daemon here, and it exposes no do-not-disturb
interface, so this cannot silence anything. What it can do is remember what
arrived, so the assistant can answer "what did I miss while I was in that call?".
Recording is off by default because notification bodies carry message previews.

Usage:
  assistant_notifications.py start            begin recording in the background
  assistant_notifications.py stop             stop recording
  assistant_notifications.py status           is it recording, and how much is stored
  assistant_notifications.py list [--since 30] [--limit 20]
  assistant_notifications.py read             mark everything as seen
  assistant_notifications.py clear            forget everything
  assistant_notifications.py focus-start 45   note that a quiet stretch began
  assistant_notifications.py focus-stop       note that it ended
  assistant_notifications.py focus-status     when the current stretch began
  assistant_notifications.py dismiss          clear the notification centre
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

MAX_ENTRIES = 200
MAX_AGE_SECONDS = 24 * 60 * 60
MAX_SUMMARY = 120
MAX_BODY = 400
RESTART_DELAY = 2
DBUS_MONITOR = "/usr/bin/dbus-monitor"


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "omarchy-assistant"


def store_path() -> Path:
    return state_dir() / "notifications.json"


def pid_path() -> Path:
    return state_dir() / "notifications.pid"


def focus_path() -> Path:
    return state_dir() / "focus-mode.json"


def write_private(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def load() -> list[dict]:
    try:
        stored = json.loads(store_path().read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [entry for entry in stored if isinstance(entry, dict)] if isinstance(stored, list) else []


def prune(entries: list[dict]) -> list[dict]:
    """Newest first, dropping anything expired or past the cap."""
    now = time.time()
    kept = [entry for entry in entries if isinstance(entry.get("at"), (int, float)) and now - entry["at"] < MAX_AGE_SECONDS]
    ordered = sorted(enumerate(kept), key=lambda pair: (pair[1].get("at") or 0, -pair[0]), reverse=True)
    return [entry for _, entry in ordered][:MAX_ENTRIES]


STRING_START = re.compile(r'^\s*string\s+"(.*)$')
SCALAR_END = re.compile(r"^\s*int32\s")


def leading_strings(lines: list[str]) -> list[str]:
    """Collect the leading string arguments, following values across lines.

    dbus-monitor prints strings with their quotes unescaped and embedded newlines
    left as real line breaks, so a value has to be read until its closing quote
    rather than one line at a time. Nothing is unescaped, because the monitor has
    already rendered the escapes.
    """
    values: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("array"):
            break
        found = STRING_START.match(line)
        if not found:
            index += 1
            continue
        rest = found.group(1)
        if rest.endswith('"'):
            values.append(rest[:-1])
            index += 1
            continue
        pieces = [rest]
        index += 1
        while index < len(lines):
            following = lines[index]
            index += 1
            if following.rstrip().endswith('"'):
                pieces.append(following.rstrip()[:-1])
                break
            pieces.append(following)
        values.append("\n".join(pieces))
    return values


def parse_call(lines: list[str]) -> dict | None:
    """Pull app, summary and body out of one dbus-monitor Notify call.

    The leading scalar values before the first array are the app name or icon, the
    summary and the body, whether the sender used the old numeric app id or the
    newer string app name, so the last two are taken by position.
    """
    leading = leading_strings(lines)
    if len(leading) < 2:
        return None
    return {
        "app": (leading[-3] if len(leading) >= 3 else "").strip(),
        "summary": leading[-2].strip()[:MAX_SUMMARY],
        "body": leading[-1].strip()[:MAX_BODY]
    }


def remember(entry: dict) -> None:
    entries = prune(load())
    if entries and entries[0].get("summary") == entry["summary"] and entries[0].get("app") == entry["app"]:
        return
    record = {"at": int(time.time()), "seen": False, **entry}
    write_private(store_path(), prune([record, *entries]))


def watch() -> int:
    """Follow Notify calls on the session bus until stopped."""
    write_private(pid_path(), {"pid": os.getpid(), "since": int(time.time())})

    def flush(lines: list[str]) -> None:
        parsed = parse_call(lines)
        if parsed and (parsed["summary"] or parsed["body"]):
            remember(parsed)

    try:
        while True:
            monitor = subprocess.Popen(
                [DBUS_MONITOR, "--session", "interface='org.freedesktop.Notifications',member='Notify'"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
            )
            assert monitor.stdout is not None
            block: list[str] = []
            expecting_notify = False
            for line in monitor.stdout:
                line = line.rstrip("\n")
                if line.startswith(("method call", "signal", "reply")):
                    # A new event also ends the previous one, so a block is flushed
                    # here as well as at its own end. Doing only one of the two
                    # loses every notification except the last.
                    if block and expecting_notify:
                        flush(block)
                    block = []
                    expecting_notify = "member=Notify" in line
                    continue
                block.append(line)
                # The expire timeout is the last argument, so the call is complete
                # and can be recorded now rather than waiting for the next event.
                if expecting_notify and SCALAR_END.match(line):
                    flush(block)
                    block = []
            monitor.wait()
            time.sleep(RESTART_DELAY)
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            if pid_path().exists():
                pid_path().unlink()
        except OSError:
            pass


def running_pid() -> int:
    try:
        pid = int(json.loads(pid_path().read_text()).get("pid") or 0)
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        return 0
    if pid <= 0:
        return 0
    try:
        os.kill(pid, 0)
    except OSError:
        return 0
    return pid


def focus_state() -> dict:
    try:
        state = json.loads(focus_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {"active": False}
    started = int(state.get("started") or 0)
    if not started or time.time() - started > MAX_AGE_SECONDS:
        return {"active": False}
    return {"active": True, "started": started, "minutes": (int(time.time()) - started) // 60}


def describe(entry: dict, number: int) -> dict:
    age = max(0, int(time.time()) - int(entry.get("at") or 0))
    return {
        "number": number,
        "age_minutes": age // 60 if age >= 3600 else (age % 3600) // 60,
        "app": str(entry.get("app") or "")[:60],
        "summary": str(entry.get("summary") or "")[:MAX_SUMMARY],
        "body": str(entry.get("body") or "")[:MAX_BODY],
        "seen": entry.get("seen") is True
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Notification history and catch-up for the assistant.")
    parser.add_argument("command", choices=["start", "stop", "status", "list", "read", "clear", "focus-start", "focus-stop", "focus-status", "dismiss", "watch"])
    parser.add_argument("minutes", nargs="?", type=int, default=0)
    parser.add_argument("--since", dest="since", type=int, default=0)
    parser.add_argument("--limit", dest="limit", type=int, default=20)
    args = parser.parse_args()

    if args.command == "start":
        existing = running_pid()
        if existing:
            print(json.dumps({"ok": True, "already_running": True, "pid": existing}))
            return 0
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "watch"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(30):
            time.sleep(0.1)
            pid = running_pid()
            if pid:
                print(json.dumps({"ok": True, "started": True, "pid": pid}))
                return 0
        print(json.dumps({"ok": False, "error": "the listener did not start"}))
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
        unread = sum(1 for entry in entries if entry.get("seen") is not True)
        print(json.dumps({"ok": True, "recording": bool(running_pid()), "entries": len(entries), "unseen": unread, "focus": focus_state()}))
        return 0

    if args.command == "clear":
        write_private(store_path(), [])
        print(json.dumps({"ok": True, "cleared": True}))
        return 0

    if args.command == "read":
        entries = prune(load())
        for entry in entries:
            entry["seen"] = True
        write_private(store_path(), entries)
        print(json.dumps({"ok": True, "marked": len(entries)}))
        return 0

    if args.command == "focus-start":
        minutes = max(1, min(args.minutes or 45, 24 * 60))
        state = {"started": int(time.time()), "minutes": minutes, "can_silence": False}
        write_private(focus_path(), state)
        print(json.dumps({"ok": True, **state, "note": "Omarchy's shell owns notifications and has no do-not-disturb control, so this records the stretch rather than silencing alerts."}))
        return 0

    if args.command == "focus-stop":
        state = focus_state()
        try:
            focus_path().unlink()
        except OSError:
            pass
        print(json.dumps({"ok": True, "was_active": state.get("active") is True, "minutes": state.get("minutes", 0)}))
        return 0

    if args.command == "focus-status":
        print(json.dumps({"ok": True, **focus_state()}))
        return 0

    if args.command == "dismiss":
        try:
            done = subprocess.run(["/usr/bin/hyprctl", "dismissnotify"], capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(json.dumps({"ok": False, "error": f"could not clear the notification centre: {error}"}))
            return 1
        if done.returncode != 0:
            print(json.dumps({"ok": False, "error": (done.stderr or done.stdout or "hyprctl failed").strip()[:200]}))
            return 1
        print(json.dumps({"ok": True, "dismissed": True}))
        return 0

    entries = prune(load())
    write_private(store_path(), entries)

    # A window of interest: an explicit --since, else the current focus stretch.
    since = 0
    if args.since > 0:
        since = int(time.time()) - args.since * 60
    else:
        state = focus_state()
        if state.get("active"):
            since = int(state.get("started") or 0)
    if since:
        entries = [entry for entry in entries if int(entry.get("at") or 0) >= since]

    listed = [describe(entry, index + 1) for index, entry in enumerate(entries)]
    limit = max(1, min(args.limit or 20, MAX_ENTRIES))
    print(json.dumps({"ok": True, "count": len(listed), "entries": listed[:limit], "recording": bool(running_pid()), "since_minutes": max(0, (int(time.time()) - since) // 60) if since else 0}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        sys.exit(watch())
    sys.exit(main())
