#!/usr/bin/env python3
import argparse
import fcntl
import json
from contextlib import contextmanager
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = {"preference", "instruction", "conversation", "project", "personal", "other"}
MAX_ENTRIES = 500
MAX_TEXT = 2000


class MemoryStoreError(Exception):
    pass


SECRET_PATTERN = re.compile(
    r"(?i)\b(?:password|passcode|api[_ -]?key|secret|private[_ -]?key|access[_ -]?token|refresh[_ -]?token|credit[_ -]?card|cvv|ssn|otp|2fa)\b"
)


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "omarchy-assistant"


def memory_path():
    return state_dir() / "memory.json"


def lock_path():
    return state_dir() / "memory.lock"


def timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalized(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def valid_category(value):
    value = str(value or "other").strip().lower()
    return value if value in CATEGORIES else "other"


def contains_secret(value):
    return bool(SECRET_PATTERN.search(str(value or "")))


def empty_data():
    return {"version": 1, "entries": []}


def read_data():
    path = memory_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return empty_data()
    except (json.JSONDecodeError, OSError) as error:
        raise MemoryStoreError(f"Memory file is unreadable: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return empty_data()
    entries = []
    for item in data["entries"]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text or contains_secret(text):
            continue
        entries.append({
            "id": str(item.get("id") or ""),
            "category": valid_category(item.get("category")),
            "text": text[:MAX_TEXT],
            "source": str(item.get("source") or "assistant")[:64],
            "created": str(item.get("created") or timestamp()),
            "updated": str(item.get("updated") or timestamp()),
        })
    return {"version": 1, "entries": entries[-MAX_ENTRIES:]}


def write_data(data):
    directory = state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".memory-", suffix=".json", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, memory_path())
        os.chmod(memory_path(), 0o600)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def locked_data(exclusive=True):
    directory = state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    lock = os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield read_data()
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            os.close(lock)


def remember(data, text, category, source):
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "Memory text cannot be empty."}
    if len(text) > MAX_TEXT:
        return {"ok": False, "error": "Memory text is too long."}
    if contains_secret(text):
        return {"ok": False, "error": "Refusing to store a likely secret or credential."}
    category = valid_category(category)
    source = str(source or "assistant").strip()[:64] or "assistant"
    key = normalized(text)
    now = timestamp()
    for entry in data["entries"]:
        if normalized(entry.get("text")) == key:
            entry["text"] = text
            entry["category"] = category
            entry["source"] = source
            entry["updated"] = now
            return {"ok": True, "entry": entry, "updated": True}
    entry = {
        "id": "m-" + secrets.token_hex(6),
        "category": category,
        "text": text,
        "source": source,
        "created": now,
        "updated": now,
    }
    data["entries"].append(entry)
    data["entries"] = data["entries"][-MAX_ENTRIES:]
    return {"ok": True, "entry": entry, "updated": False}


def list_entries(data, category, limit):
    category = str(category or "").strip().lower()
    entries = [entry for entry in data["entries"] if not category or entry["category"] == category]
    entries.sort(key=lambda entry: entry.get("updated", ""), reverse=True)
    return {"ok": True, "entries": entries[:max(1, min(int(limit), MAX_ENTRIES))]}


def forget(data, entry_id):
    entry_id = str(entry_id or "").strip()
    before = len(data["entries"])
    data["entries"] = [entry for entry in data["entries"] if entry.get("id") != entry_id]
    return {"ok": True, "removed": before - len(data["entries"])}


def clear(data, category):
    category = str(category or "").strip().lower()
    before = len(data["entries"])
    if category:
        data["entries"] = [entry for entry in data["entries"] if entry.get("category") != category]
    else:
        data["entries"] = []
    return {"ok": True, "removed": before - len(data["entries"])}


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--category", default="")
    list_parser.add_argument("--limit", type=int, default=32)
    remember_parser = subparsers.add_parser("remember")
    remember_parser.add_argument("--text", required=True)
    remember_parser.add_argument("--category", default="other")
    remember_parser.add_argument("--source", default="assistant")
    forget_parser = subparsers.add_parser("forget")
    forget_parser.add_argument("--id", required=True)
    clear_parser = subparsers.add_parser("clear")
    clear_parser.add_argument("--category", default="")
    args = parser.parse_args()

    with locked_data(exclusive=args.command != "list") as data:
        if args.command == "list":
            result = list_entries(data, args.category, args.limit)
        elif args.command == "remember":
            result = remember(data, args.text, args.category, args.source)
            if result.get("ok"):
                write_data(data)
        elif args.command == "forget":
            result = forget(data, args.id)
            if result.get("ok"):
                write_data(data)
        elif args.command == "clear":
            result = clear(data, args.category)
            if result.get("ok"):
                write_data(data)
        else:
            result = {"ok": False, "error": "Unknown command."}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1)
