#!/usr/bin/env python3
"""Recipes: named, multi-step procedures the assistant can follow later.

A recipe is a short list of steps the user described once, in conversation, and
approved. This module owns the format, validates it, and renders it for review.
It does not execute anything yet: `list`, `show`, `dry-run`, `validate`, `save`
and `delete` only.

Recipes live in ~/.config/omarchy-assistant/recipes as JSON, because they are
drafted by the assistant rather than typed by hand, and a parser with no
dependencies is one less thing between a draft and the disk.

Format:
  {
    "name": "install-steam-game",          lowercase, letters, digits, dashes
    "title": "Install a Steam game",        what the user sees
    "description": "one line about when to use it",
    "vars": ["appid"],                      placeholders the user supplies at run time
    "steps": [
      {"say": "Opening Steam"},
      {"open": "steam"},
      {"command": ["steam", "steam://install/{{appid}}"]},
      {"ask": "Steam may need a password or a purchase. Do that part yourself."},
      {"wait_for": "Downloading", "timeout_seconds": 300},
      {"type": {"text": "hello {{person}}", "target": "the message box"}},
      {"press": "Enter"},
      {"click": "the search box"},
      {"wait": 2}
    ]
  }

Usage:
  assistant_recipes.py list
  assistant_recipes.py show NAME
  assistant_recipes.py dry-run NAME [--var key=value]...
  assistant_recipes.py run NAME [--var key=value]...    resolved steps, or why not
  assistant_recipes.py progress                          where a paused recipe got to
  assistant_recipes.py progress '<json>'                 record progress
  assistant_recipes.py progress --clear                  forget progress
  assistant_recipes.py validate '<json>'
  assistant_recipes.py save '<json>'
  assistant_recipes.py delete NAME
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,47}[a-z0-9])?$")
PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
MAX_BYTES = 64 * 1024
MAX_STEPS = 40
# Steps that do something to the machine, as opposed to describing or waiting.
ACTING = {"open", "type", "press", "click", "command"}
# Characters that only mean something to a shell. Commands run as an argument
# list, never through a shell, so a step containing these is a mistake worth
# reporting rather than a clever trick to preserve.
SHELL_OPERATORS = (";", "&&", "||", "|", "$(", "`", ">", "<", "&")


def recipe_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "omarchy-assistant" / "recipes"


def problems(recipe: Any) -> list[str]:
    """Return everything wrong with a draft. An empty list means it is usable."""
    found: list[str] = []
    if not isinstance(recipe, dict):
        return ["A recipe must be an object."]

    name = recipe.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        found.append("name must be lowercase letters, digits and dashes, like install-steam-game")

    title = recipe.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 80:
        found.append("title must be a short non-empty line")

    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        found.append("steps must be a non-empty list")
        return found
    if len(steps) > MAX_STEPS:
        found.append(f"a recipe may have at most {MAX_STEPS} steps, this has {len(steps)}")

    declared = recipe.get("vars") or []
    if not isinstance(declared, list) or any(not isinstance(v, str) or not re.match(r"^[a-z0-9_]+$", v) for v in declared):
        found.append("vars must be a list of lowercase names like appid")
        declared = []

    used: set[str] = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            found.append(f"step {index} is not an object")
            continue
        keys = [key for key in step if key != "timeout_seconds"]
        if len(keys) != 1:
            found.append(f"step {index} must have exactly one action, found {keys or 'none'}")
            continue
        action = keys[0]
        value = step[action]
        used.update(PLACEHOLDER.findall(json.dumps(value)))

        if action == "open":
            if not isinstance(value, str) or not value.strip():
                found.append(f"step {index}: open needs an application name")
        elif action == "command":
            if not isinstance(value, list) or not value or any(not isinstance(part, str) or not part for part in value):
                found.append(f"step {index}: command must be a list of strings, like [\"steam\", \"steam://install/1\"]")
            else:
                joined = " ".join(value)
                for operator in SHELL_OPERATORS:
                    if operator in joined:
                        found.append(f"step {index}: command contains {operator!r}. Commands run as an argument list, not through a shell, so split it into separate arguments instead.")
                        break
        elif action in ("type",):
            if not isinstance(value, dict) or not isinstance(value.get("text"), str) or not value.get("text", "").strip():
                found.append(f"step {index}: type needs {{\"text\": \"...\", \"target\": \"...\"}}")
            elif not isinstance(value.get("target"), str) or not value.get("target", "").strip():
                found.append(f"step {index}: type needs to say which field it is typing into")
        elif action in ("press", "click", "say", "ask", "wait_for"):
            if not isinstance(value, str) or not value.strip():
                found.append(f"step {index}: {action} needs some text")
        elif action == "wait":
            seconds = value
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0 or seconds > 600:
                found.append(f"step {index}: wait must be a number of seconds between 0 and 600")
        else:
            found.append(f"step {index}: unknown step type {action!r}")

        if action == "wait_for":
            timeout = step.get("timeout_seconds", 60)
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 1 or timeout > 3600:
                found.append(f"step {index}: timeout_seconds must be between 1 and 3600")

    undeclared = sorted(used - set(declared) - {"name"})
    if undeclared:
        found.append("these placeholders are used but not listed in vars: " + ", ".join(undeclared))
    unused = sorted(set(declared) - used)
    if unused:
        found.append("these vars are listed but never used: " + ", ".join(unused))
    return found


def render(recipe: dict, values: dict[str, str] | None = None) -> list[str]:
    """One readable line per step, with placeholders filled in for review."""
    values = values or {}
    lines: list[str] = []
    for index, step in enumerate(recipe.get("steps") or [], start=1):
        keys = [key for key in step if key != "timeout_seconds"]
        action = keys[0] if keys else "?"
        value = step.get(action)
        if action == "command":
            parts = [fill(str(part), values) for part in value] if isinstance(value, list) else [str(value)]
            lines.append(f"{index}. run: {' '.join(parts)}")
        elif action == "type" and isinstance(value, dict):
            text = fill(str(value.get("text", "")), values).replace("\n", " ")
            lines.append(f"{index}. type into {fill(str(value.get('target', '')), values)}: {text!r}")
        elif action == "open":
            lines.append(f"{index}. open {fill(str(value), values)}")
        elif action == "click":
            lines.append(f"{index}. click {fill(str(value), values)}")
        elif action == "press":
            lines.append(f"{index}. press {fill(str(value), values)}")
        elif action == "wait":
            lines.append(f"{index}. wait {value}s")
        elif action == "wait_for":
            lines.append(f"{index}. wait for {fill(str(value), values)!r} (up to {step.get('timeout_seconds', 60)}s)")
        elif action == "ask":
            lines.append(f"{index}. STOP AND ASK: {fill(str(value), values)}")
        elif action == "say":
            lines.append(f"{index}. say: {fill(str(value), values)}")
        else:
            lines.append(f"{index}. {action}: {value}")
    return lines


def fill(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def progress_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "omarchy-assistant" / "recipe-progress.json"


def read_progress() -> dict | None:
    try:
        stored = json.loads(progress_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return stored if isinstance(stored, dict) else None


def write_progress(payload: dict) -> None:
    path = progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def fill(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def resolve_steps(recipe: dict, values: dict[str, str]) -> list[dict]:
    """The steps with placeholders filled in, ready to hand to the desktop app."""
    resolved: list[dict] = []
    for step in recipe.get("steps") or []:
        if not isinstance(step, dict):
            continue
        action = next((key for key in step if key != "timeout_seconds"), "")
        value = step.get(action)
        if isinstance(value, str):
            value = fill(value, values)
        elif isinstance(value, dict):
            value = {key: fill(str(item), values) if isinstance(item, str) else item for key, item in value.items()}
        elif isinstance(value, list) and action == "command":
            value = [fill(str(item), values) for item in value]
        entry: dict[str, Any] = {action: value}
        if "timeout_seconds" in step:
            entry["timeout_seconds"] = step["timeout_seconds"]
        resolved.append(entry)
    return resolved


# Steps the desktop app knows how to carry out. Anything else is refused outright
# rather than skipped, so a recipe never quietly does less than it says.
SUPPORTED = {"say", "open", "click", "type", "press", "wait", "ask"}
# Accepted when a recipe is written, but not runnable yet.
PENDING = {
    "command": "commands are not runnable yet",
}


def unsupported(recipe: dict) -> dict[str, int]:
    found: dict[str, int] = {}
    for step in recipe.get("steps") or []:
        if not isinstance(step, dict):
            continue
        action = next((key for key in step if key != "timeout_seconds"), "")
        if action in PENDING:
            found[action] = found.get(action, 0) + 1
    return found


def load_all() -> list[dict]:
    directory = recipe_dir()
    if not directory.is_dir():
        return []
    found: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        try:
            recipe = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(recipe, dict):
            recipe["_path"] = str(path)
            found.append(recipe)
    return found


def read(name: str) -> dict | None:
    path = recipe_dir() / f"{name}.json"
    if not path.is_file():
        return None
    try:
        recipe = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return recipe if isinstance(recipe, dict) else None


def write(recipe: dict) -> tuple[bool, str]:
    path = recipe_dir() / f"{recipe['name']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {key: value for key, value in recipe.items() if not key.startswith("_")}
    stored.setdefault("created", int(time.time()))
    stored["updated"] = int(time.time())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    os.chmod(path, 0o600)
    return True, str(path)


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, separators=(",", ":")))
    return 0 if payload.get("ok") is True else 1


def fail(error: str, **extra: Any) -> int:
    payload: dict[str, Any] = {"ok": False, "error": error[:400]}
    payload.update(extra)
    return emit(payload)


def summarise(recipe: dict) -> dict:
    steps = recipe.get("steps") or []
    kinds = [next((key for key in step if key != "timeout_seconds"), "?") for step in steps if isinstance(step, dict)]
    return {
        "name": str(recipe.get("name") or ""),
        "title": str(recipe.get("title") or ""),
        "description": str(recipe.get("description") or ""),
        "steps": len(steps),
        "commands": kinds.count("command"),
        "gates": kinds.count("ask"),
        "vars": recipe.get("vars") or [],
        "author": str(recipe.get("author") or "user"),
        "updated": recipe.get("updated")
    }


def parse_vars(pairs: list[str]) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    bad: list[str] = []
    for pair in pairs:
        if "=" not in pair:
            bad.append(f"{pair} is not key=value")
            continue
        key, _, value = pair.partition("=")
        values[key.strip()] = value
    return values, bad


def main() -> int:
    parser = argparse.ArgumentParser(description="Recipes for the Omarchy Assistant.")
    parser.add_argument("command", choices=["list", "show", "dry-run", "run", "progress", "validate", "save", "delete"])
    parser.add_argument("payload", nargs="?", default="")
    parser.add_argument("--var", dest="vars", action="append", default=[])
    parser.add_argument("--clear", dest="clear", action="store_true")
    args = parser.parse_args()

    if args.command == "list":
        return emit({"ok": True, "count": len(recipes := load_all()), "recipes": [summarise(r) for r in recipes]})

    if args.command == "progress":
        if args.clear:
            path = progress_path()
            had = path.is_file()
            if had:
                try:
                    path.unlink()
                except OSError:
                    pass
            return emit({"ok": True, "cleared": had})
        if args.payload:
            try:
                payload = json.loads(args.payload)
            except json.JSONDecodeError as error:
                return fail(f"the progress could not be read: {error}")
            if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
                return fail("progress needs at least a recipe name")
            payload["updated"] = int(time.time())
            write_progress(payload)
            return emit({"ok": True, "progress": payload})
        path = progress_path()
        had = path.is_file()
        if args.payload == "--clear" and had:
            try:
                path.unlink()
            except OSError:
                pass
            return emit({"ok": True, "cleared": True})
        return emit({"ok": True, "progress": None if not had else read_progress()})

    if args.command == "run":
        name = args.payload or ""
        if not NAME_PATTERN.match(name):
            return fail("that is not a recipe name")
        recipe = read(name)
        if recipe is None:
            return fail("there is no recipe with that name")
        blocked = unsupported(recipe)
        if blocked:
            return emit({
                "ok": False,
                "error": "this recipe cannot run yet",
                "unsupported": blocked,
                "detail": "; ".join(f"{count} {kind} step" + ("s" if count != 1 else "") + f": {PENDING[kind]}" for kind, count in sorted(blocked.items()))
            })
        values, bad = parse_vars(args.vars)
        if bad:
            return fail("; ".join(bad))
        missing = sorted(set(recipe.get("vars") or []) - set(values))
        if missing:
            return emit({"ok": False, "error": "this recipe needs values before it can run", "missing": missing, "vars": recipe.get("vars") or []})
        steps = resolve_steps(recipe, values)
        return emit({"ok": True, "name": name, "title": str(recipe.get("title") or name), "steps": steps, "total": len(steps), "lines": render(recipe, values), "summary": summarise(recipe), "values": values})

    if args.command == "delete":
        if not NAME_PATTERN.match(args.payload or ""):
            return fail("that is not a recipe name")
        path = recipe_dir() / f"{args.payload}.json"
        if not path.is_file():
            return fail("there is no recipe with that name")
        path.unlink()
        return emit({"ok": True, "deleted": args.payload})

    if args.command in ("validate", "save"):
        raw = args.payload or sys.stdin.read()
        if len(raw) > MAX_BYTES:
            return fail("that recipe is too large")
        try:
            draft = json.loads(raw)
        except json.JSONDecodeError as error:
            return fail(f"the recipe is not valid JSON: {error}")
        found = problems(draft)
        if found:
            return emit({"ok": False, "error": "the recipe needs fixing", "problems": found})
        if args.command == "validate":
            return emit({"ok": True, "valid": True, "name": draft["name"], "steps": len(draft["steps"]), "lines": render(draft)})
        draft.setdefault("author", "assistant")
        draft.setdefault("vars", [])
        draft.setdefault("description", "")
        saved, path = write(draft)
        return emit({"ok": saved, "name": draft["name"], "path": path, "summary": summarise(draft), "lines": render(draft)})

    recipe = read(args.payload or "")
    if recipe is None:
        return fail("there is no recipe with that name", names=[r.get("name") for r in load_all()])

    if args.command == "show":
        return emit({"ok": True, "recipe": summarise(recipe), "lines": render(recipe), "recipe_json": recipe})

    values, bad = parse_vars(args.vars)
    if bad:
        return fail("; ".join(bad))
    missing = sorted(set(recipe.get("vars") or []) - set(values))
    if missing:
        return emit({"ok": False, "error": "this recipe needs values before it can be shown with them filled in", "missing": missing, "vars": recipe.get("vars") or []})
    return emit({"ok": True, "recipe": summarise(recipe), "values": values, "lines": render(recipe, values), "steps": resolve_steps(recipe, values), "total": len(recipe.get("steps") or []), "runs_commands": any(next((k for k in s if k != "timeout_seconds"), "") == "command" for s in recipe.get("steps") or [])})


if __name__ == "__main__":
    sys.exit(main())
