#!/usr/bin/env python3
"""Window and workspace control for the Omarchy Assistant, through hyprctl.

`plan` only reads state. It resolves which window the model means and returns a
proposal for the desktop app to show the user. `run` performs one approved change
and re-checks that the window it was planned for is still on screen, so a window
that was closed in the meantime is never acted on by accident.

Dispatcher names and their arguments are built here rather than passed through, so
the model cannot reach an arbitrary hyprctl command. There is deliberately no close,
kill, or float-everything operation: every change here can be undone by the user.

Usage:
  assistant_windows.py plan list
  assistant_windows.py plan focus TARGET
  assistant_windows.py plan move TARGET --to DEST
  assistant_windows.py plan swap TARGET OTHER
  assistant_windows.py plan resize TARGET --width N --height N
  assistant_windows.py plan fullscreen TARGET
  assistant_windows.py plan workspace DEST
  assistant_windows.py plan monitor DEST
  assistant_windows.py plan tile TARGET [TARGET ...] --direction left|right
  assistant_windows.py plan scratchpad [TARGET]
  assistant_windows.py run '{"op": "focus", "address": "0x..."}'
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

HYPRCTL = "/usr/bin/hyprctl"
TIMEOUT = 12
MAX_TITLE = 60
MAX_WINDOWS = 40
LEFT, TOP, RIGHT, BOTTOM = 0, 1, 2, 3  # hyprctl reports reserved as left, top, right, bottom
GAP = 8


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, separators=(",", ":")))
    return 0 if payload.get("ok") is True else 1


def fail(error: str, **extra: Any) -> int:
    payload: dict[str, Any] = {"ok": False, "error": error[:400]}
    payload.update(extra)
    return emit(payload)


def hyprctl(*args: str) -> Any:
    """Run hyprctl and return parsed JSON, or raise RuntimeError with its message."""
    try:
        done = subprocess.run(
            [HYPRCTL, *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"hyprctl is unavailable: {error}") from error
    if done.returncode != 0:
        message = (done.stderr or done.stdout or "").strip().splitlines()
        raise RuntimeError(message[0] if message else f"hyprctl exited {done.returncode}")
    try:
        return json.loads(done.stdout or "null")
    except json.JSONDecodeError as error:
        raise RuntimeError(f"hyprctl returned unreadable output: {error}") from error


def lua_string(value: object) -> str:
    """Quote a value for Lua. Non-ASCII becomes decimal escapes so any title works."""
    out = ['"']
    for character in str(value):
        if character == '"':
            out.append('\\"')
        elif character == "\\":
            out.append("\\\\")
        elif ord(character) < 32 or ord(character) > 126:
            out.append("\\%d" % ord(character))
        else:
            out.append(character)
    out.append('"')
    return "".join(out)


def dispatch(body: str) -> tuple[bool, str]:
    """Run one hl.dsp dispatcher. This Hyprland only accepts Lua through `hyprctl eval`."""
    try:
        done = subprocess.run(
            [HYPRCTL, "eval", "hl.dispatch(" + body + ")"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"hyprctl is unavailable: {error}"
    output = ((done.stdout or "") + (done.stderr or "")).strip()
    if done.returncode != 0 or output.lower().startswith("error"):
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return False, (lines[0] if lines else f"hyprctl exited {done.returncode}")[:300]
    return True, ""


def snapshot() -> tuple[list[dict], list[dict], list[dict]]:
    clients = hyprctl("-j", "clients") or []
    monitors = hyprctl("-j", "monitors") or []
    workspaces = hyprctl("-j", "workspaces") or []
    if not isinstance(clients, list) or not isinstance(monitors, list) or not isinstance(workspaces, list):
        raise RuntimeError("hyprctl returned an unexpected shape")
    return clients, monitors, workspaces


def monitor_name(monitor: dict) -> str:
    return str(monitor.get("name") or "")


def monitor_id(value: object) -> int:
    """Hyprland monitor ids start at 0, so 0 must not read as 'missing'."""
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return -1


def logical_size(monitor: dict) -> tuple[int, int]:
    """Hyprland reports monitor width and height in physical pixels, positions in logical."""
    try:
        scale = float(monitor.get("scale") or 1) or 1.0
    except (TypeError, ValueError):
        scale = 1.0
    width = int(round(float(monitor.get("width") or 0) / scale))
    height = int(round(float(monitor.get("height") or 0) / scale))
    return width, height


def reservations(monitor: dict) -> list[int]:
    values = monitor.get("reserved")
    if isinstance(values, list) and len(values) == 4:
        try:
            return [int(value) for value in values]
        except (TypeError, ValueError):
            pass
    return [0, 0, 0, 0]


def work_area(monitor: dict) -> tuple[int, int, int, int]:
    """Usable area of a monitor in logical coordinates, minus reserved edges."""
    width, height = logical_size(monitor)
    pad = reservations(monitor)
    x0 = int(monitor.get("x") or 0) + pad[LEFT]
    y0 = int(monitor.get("y") or 0) + pad[TOP]
    x1 = x0 + max(width - pad[LEFT] - pad[RIGHT], 100)
    y1 = y0 + max(height - pad[TOP] - pad[BOTTOM], 100)
    return x0, y0, x1, y1


def describe(client: dict) -> str:
    title = str(client.get("title") or "").strip()
    label = str(client.get("class") or "window")
    return f"{label} ({title[:MAX_TITLE]})" if title else label


def candidates(clients: list[dict]) -> list[dict]:
    brief = []
    for client in clients[:MAX_WINDOWS]:
        at = client.get("at") or [0, 0]
        size = client.get("size") or [0, 0]
        brief.append(
            {
                "class": str(client.get("class") or ""),
                "title": str(client.get("title") or "")[:MAX_TITLE],
                "workspace": str((client.get("workspace") or {}).get("name") or ""),
                "monitor": monitor_id(client.get("monitor")),
                "at": [int(at[0]), int(at[1])],
                "size": [int(size[0]), int(size[1])],
                "floating": client.get("floating") == 1,
            }
        )
    return brief


def resolve(target: str, clients: list[dict]) -> dict:
    """Find the one window the target names, or explain why it is ambiguous."""
    needle = str(target or "").strip()
    if not needle:
        raise RuntimeError("No window was named.")
    if needle.lower().startswith("0x"):
        for client in clients:
            if str(client.get("address") or "").lower() == needle.lower():
                return client
        raise RuntimeError(f"No window with address {needle} is open.")

    exact_class = [c for c in clients if str(c.get("class") or "").lower() == needle.lower()]
    if len(exact_class) == 1:
        return exact_class[0]
    part_class = [c for c in clients if needle.lower() in str(c.get("class") or "").lower()]
    if len(part_class) == 1:
        return part_class[0]
    part_title = [c for c in clients if needle.lower() in str(c.get("title") or "").lower()]
    if len(part_title) == 1:
        return part_title[0]

    matches = exact_class or part_class or part_title
    if not matches:
        raise RuntimeError(f"No open window matches '{needle}'.")
    names = sorted({describe(c) for c in matches})
    if len(matches) > 1 and len(names) == 1:
        raise RuntimeError(f"'{needle}' matches {len(matches)} windows all titled {names[0]}. Name a class or a longer piece of the title.")
    raise RuntimeError(f"'{needle}' is ambiguous: " + ", ".join(names[:6]))


def find_monitor(destination: str, monitors: list[dict]) -> dict:
    needle = str(destination or "").strip()
    if not needle:
        raise RuntimeError("No monitor was named.")
    if needle.lower() in ("focused", "focus", "current"):
        for monitor in monitors:
            if monitor.get("focused") is True:
                return monitor
        raise RuntimeError("Hyprland reported no focused monitor.")
    if needle.lower() in ("left", "right"):
        ordered = sorted(monitors, key=lambda m: (int(m.get("x") or 0), int(m.get("y") or 0)))
        return ordered[0] if needle.lower() == "left" else ordered[-1]
    for monitor in monitors:
        if monitor_name(monitor).lower() == needle.lower():
            return monitor
    available = ", ".join(monitor_name(m) for m in monitors)
    raise RuntimeError(f"No monitor named '{needle}'. Available: {available}.")


def strip_prefix(destination: str, prefix: str) -> str:
    """Accept both 'workspace:3' and a bare '3' so the model can be sloppy."""
    value = str(destination or "").strip()
    if value.lower().startswith(prefix + ":"):
        return value.split(":", 1)[1].strip()
    return value


def find_workspace(destination: str, workspaces: list[dict]) -> str:
    needle = str(destination or "").strip()
    if not needle:
        raise RuntimeError("No workspace was named.")
    if needle.lower() in ("next", "prev", "previous", "last"):
        return needle.lower()
    if needle.isdigit() or (len(needle) > 1 and needle[1:].isdigit() and needle[0] in "+-"):
        return needle
    for workspace in workspaces:
        if str(workspace.get("name") or "") == needle:
            return needle
    named = [str(w.get("name") or "") for w in workspaces]
    raise RuntimeError(f"No workspace named '{needle}'. Open workspaces: {', '.join(named) if named else 'none'}.")


def plan(op: str, args: argparse.Namespace) -> dict[str, Any]:
    clients, monitors, workspaces = snapshot()
    if op == "list":
        return {"ok": True, "op": op, "windows": candidates(clients)}
    if not clients and op not in ("list", "workspace", "monitor"):
        raise RuntimeError("There are no open windows.")

    if op == "focus":
        client = resolve(args.target, clients)
        return {
            "ok": True,
            "op": op,
            "address": str(client.get("address")),
            "summary": f"Focus {describe(client)}",
            "detail": f"workspace {(client.get('workspace') or {}).get('name')}",
            "requires_approval": True,
        }
    if op == "move":
        client = resolve(args.target, clients)
        destination = str(args.to or "").strip()
        if destination.lower() == "scratchpad":
            return {
                "ok": True,
                "op": "scratchpad",
                "address": str(client.get("address")),
                "name": "minimized",
                "summary": f"Send {describe(client)} to the scratchpad",
                "detail": "the special workspace",
                "requires_approval": True,
            }
        if destination.lower().startswith("monitor:"):
            monitor = find_monitor(destination.split(":", 1)[1], monitors)
            return {
                "ok": True,
                "op": "movewindow",
                "address": str(client.get("address")),
                "monitor": monitor_name(monitor),
                "summary": f"Move {describe(client)} to {monitor_name(monitor)}",
                "detail": f"from workspace {(client.get('workspace') or {}).get('name')}",
                "requires_approval": True,
            }
        workspace = find_workspace(strip_prefix(destination, "workspace"), workspaces)
        return {
            "ok": True,
            "op": "movewindow",
            "address": str(client.get("address")),
            "workspace": workspace,
            "summary": f"Move {describe(client)} to workspace {workspace}",
            "detail": f"from workspace {(client.get('workspace') or {}).get('name')}",
            "requires_approval": True,
        }
    if op == "swap":
        first = resolve(args.target, clients)
        second = resolve(args.other, clients)
        if str(first.get("address")) == str(second.get("address")):
            raise RuntimeError("Those are the same window.")
        return {
            "ok": True,
            "op": "swapwindow",
            "address": str(first.get("address")),
            "other": str(second.get("address")),
            "summary": f"Swap {describe(first)} with {describe(second)}",
            "detail": "their positions",
            "requires_approval": True,
        }
    if op == "resize":
        client = resolve(args.target, clients)
        size = client.get("size") or [0, 0]
        width = int(args.width) if args.width else int(size[0])
        height = int(args.height) if args.height else int(size[1])
        if width < 200 or height < 150 or width > 20000 or height > 20000:
            raise RuntimeError("A window size must be between 200x150 and 20000x20000.")
        return {
            "ok": True,
            "op": "resizewindow",
            "address": str(client.get("address")),
            "width": width,
            "height": height,
            "summary": f"Resize {describe(client)} to {width}x{height}",
            "detail": f"from {int(size[0])}x{int(size[1])}",
            "requires_approval": True,
        }
    if op == "float":
        client = resolve(args.target, clients)
        action = str(args.to or "toggle").lower()
        if action not in ("toggle", "enable", "disable"):
            raise RuntimeError("Float action must be toggle, enable, or disable.")
        words = {"toggle": "Toggle floating on", "enable": "Float", "disable": "Unfloat"}[action]
        return {
            "ok": True,
            "op": "float",
            "address": str(client.get("address")),
            "action": action,
            "summary": f"{words} {describe(client)}",
            "detail": "floating" if client.get("floating") == 1 else "tiled",
            "requires_approval": True,
        }
    if op == "fullscreen":
        client = resolve(args.target, clients)
        return {
            "ok": True,
            "op": "fullscreen",
            "address": str(client.get("address")),
            "summary": f"Fullscreen {describe(client)}",
            "detail": "toggle",
            "requires_approval": True,
        }
    if op == "workspace":
        destination = str(args.to or args.target or "").strip()
        if destination.lower().startswith("monitor:"):
            monitor = find_monitor(destination.split(":", 1)[1], monitors)
            return {
                "ok": True,
                "op": "focusmonitor",
                "monitor": monitor_name(monitor),
                "summary": f"Switch to {monitor_name(monitor)}",
                "detail": f"workspace {(monitor.get('activeWorkspace') or {}).get('name')}",
                "requires_approval": True,
            }
        workspace = find_workspace(strip_prefix(destination, "workspace"), workspaces)
        return {
            "ok": True,
            "op": "workspace",
            "workspace": workspace,
            "summary": f"Switch to workspace {workspace}",
            "detail": "",
            "requires_approval": True,
        }
    if op == "monitor":
        monitor = find_monitor(args.to or args.target, monitors)
        return {
            "ok": True,
            "op": "focusmonitor",
            "monitor": monitor_name(monitor),
            "summary": f"Switch to {monitor_name(monitor)}",
            "detail": f"workspace {(monitor.get('activeWorkspace') or {}).get('name')}",
            "requires_approval": True,
        }
    if op == "scratchpad":
        if not args.target:
            return {
                "ok": True,
                "op": "togglespecial",
                "name": "minimized",
                "summary": "Show or hide the scratchpad",
                "detail": "special workspace",
                "requires_approval": True,
            }
        client = resolve(args.target, clients)
        return {
            "ok": True,
            "op": "movetospecial",
            "address": str(client.get("address")),
            "name": "minimized",
            "summary": f"Send {describe(client)} to the scratchpad",
            "detail": "the special workspace",
            "requires_approval": True,
        }
    if op == "tile":
        targets = [resolve(name, clients) for name in args.target]
        unique: list[dict] = []
        for client in targets:
            if all(str(client.get("address")) != str(seen.get("address")) for seen in unique):
                unique.append(client)
        if len(unique) < 2:
            raise RuntimeError("Tiling needs at least two different windows.")
        monitor = next((m for m in monitors if monitor_id(m.get("id")) == monitor_id(unique[0].get("monitor"))), None)
        if monitor is None:
            raise RuntimeError("The first window is not on a known monitor.")
        if any(monitor_id(c.get("monitor")) != monitor_id(monitor.get("id")) for c in unique):
            spread = sorted({monitor_name(m) for m in monitors if monitor_id(m.get("id")) in {monitor_id(c.get("monitor")) for c in unique}})
            raise RuntimeError("Tiling needs its windows on one monitor, but these are spread over " + ", ".join(spread) + ". Move them together first.")
        direction = str(args.direction or "left").lower()
        if direction not in ("left", "right"):
            raise RuntimeError("Tile direction must be left or right.")
        listed = ", ".join(describe(c) for c in unique)
        return {
            "ok": True,
            "op": "tile",
            "addresses": [str(c.get("address")) for c in unique],
            "monitor": monitor_name(monitor),
            "direction": direction,
            "summary": f"Tile {len(unique)} windows on {monitor_name(monitor)}: {listed}",
            "detail": "floated into equal columns, unfloat to undo",
            "requires_approval": True,
        }
    raise RuntimeError(f"Unknown operation: {op}")


def run(payload: dict[str, Any]) -> dict[str, Any]:
    op = str(payload.get("op") or "")
    if op == "focus":
        return _one(f"hl.dsp.focus({{ window = {selector(payload.get('address'))} }})")
    if op == "movewindow":
        window = selector(payload.get("address"))
        if payload.get("monitor"):
            return _one(f"hl.dsp.window.move({{ monitor = {lua_string(payload['monitor'])}, follow = true, window = {window} }})")
        return _one(f"hl.dsp.window.move({{ workspace = {lua_string(payload['workspace'])}, follow = true, window = {window} }})")
    if op == "swapwindow":
        return _one(f"hl.dsp.window.swap({{ target = {selector(payload.get('other'))}, window = {selector(payload.get('address'))} }})")
    if op == "resizewindow":
        window = selector(payload.get("address"))
        return _one(f"hl.dsp.window.resize({{ x = {int(payload['width'])}, y = {int(payload['height'])}, window = {window} }})")
    if op == "float":
        action = str(payload.get("action") or "toggle")
        return _one(f'hl.dsp.window.float({{ action = {lua_string(action)}, window = {selector(payload.get("address"))} }})')
    if op == "fullscreen":
        return _one(f"hl.dsp.window.fullscreen({{ window = {selector(payload.get('address'))} }})")
    if op == "workspace":
        return _one(f"hl.dsp.focus({{ workspace = {lua_string(payload['workspace'])} }})")
    if op == "focusmonitor":
        return _one(f"hl.dsp.focus({{ monitor = {lua_string(payload['monitor'])} }})")
    if op == "movetospecial":
        return _one(f"hl.dsp.window.move({{ workspace = {lua_string('special:' + str(payload.get('name') or 'minimized'))}, window = {selector(payload.get('address'))} }})")
    if op == "togglespecial":
        return _one(f"hl.dsp.workspace.toggle_special({lua_string(str(payload.get('name') or 'minimized'))})")
    if op == "tile":
        return _tile(payload)
    return {"ok": False, "error": f"Unknown operation: {op}"}


def selector(value: object) -> str:
    """Address a window by its exact address, which is what a plan recorded."""
    return lua_string("address:" + str(value or ""))


def _one(body: str) -> dict[str, Any]:
    done, error = dispatch(body)
    if not done:
        return {"ok": False, "error": error[:400]}
    return {"ok": True}


def _tile(payload: dict[str, Any]) -> dict[str, Any]:
    addresses = [str(a) for a in payload.get("addresses") or []]
    monitor_name_wanted = str(payload.get("monitor") or "")
    if len(addresses) < 2:
        return {"ok": False, "error": "Tiling needs at least two windows."}
    clients, monitors, _workspaces = snapshot()
    live = {str(c.get("address")): c for c in clients}
    missing = [a for a in addresses if a not in live]
    if missing:
        return {"ok": False, "error": "One of those windows was closed before the tiling ran."}
    monitor = next((m for m in monitors if monitor_name(m) == monitor_name_wanted), None)
    if monitor is None:
        return {"ok": False, "error": "That monitor is no longer available."}
    if monitor_id(live[addresses[0]].get("monitor")) != monitor_id(monitor.get("id")):
        return {"ok": False, "error": "Those windows are on different monitors now."}
    x0, y0, x1, y1 = work_area(monitor)
    total = x1 - x0
    column = max(total // len(addresses) - GAP, 200)
    for index, address in enumerate(addresses):
        # Dwindle will not honour exact coordinates for a tiled window, so float it first.
        done, error = dispatch(f'hl.dsp.window.float({{ action = "enable", window = {selector(address)} }})')
        if not done:
            return {"ok": False, "error": error[:400]}
        done, error = dispatch(f"hl.dsp.window.resize({{ x = {column}, y = {y1 - y0}, window = {selector(address)} }})")
        if not done:
            return {"ok": False, "error": error[:400]}
        left = x0 + index * (column + GAP)
        if str(payload.get("direction") or "left") == "right":
            left = x1 - (index + 1) * column - index * GAP
        done, error = dispatch(f"hl.dsp.window.move({{ x = {left}, y = {y0}, window = {selector(address)} }})")
        if not done:
            return {"ok": False, "error": error[:400]}
    return {"ok": True}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and run Hyprland window actions for the assistant.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_plan(name: str, help_text: str) -> argparse.ArgumentParser:
        item = sub.add_parser(name, help=help_text)
        item.add_argument("op", choices=["list", "focus", "move", "swap", "resize", "float", "fullscreen", "workspace", "monitor", "scratchpad", "tile"])
        item.add_argument("targets", nargs="*", default=[], help="window names, or several separated by commas")
        item.add_argument("--to", dest="to", default="")
        item.add_argument("--other", dest="other", default="")
        item.add_argument("--width", dest="width", type=int, default=0)
        item.add_argument("--height", dest="height", type=int, default=0)
        item.add_argument("--direction", dest="direction", default="left")
        return item

    add_plan("plan", "read-only: resolve a window and return a proposal")
    run_parser = sub.add_parser("run", help="perform one approved action")
    run_parser.add_argument("payload", help="JSON object from a plan")
    return parser


def split_targets(op: str, raw: list[str], other: str) -> tuple[object, str]:
    """Accept 'a b', 'a,b' or a mixture, and hand plan() the shape it expects."""
    parts: list[str] = []
    for chunk in list(raw) + ([other] if other else []):
        parts.extend(piece.strip() for piece in str(chunk).split(",") if piece.strip())
    if op == "tile":
        return parts, ""
    if op == "swap":
        if len(parts) < 2:
            raise RuntimeError("Swapping needs two windows.")
        return parts[0], parts[1]
    return (parts[0] if parts else ""), ""


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "run":
            try:
                payload = json.loads(args.payload)
            except json.JSONDecodeError as error:
                return fail(f"The plan could not be read: {error}")
            if not isinstance(payload, dict):
                return fail("The plan was not an object.")
            return emit(run(payload))
        if args.op == "tile" and not args.targets:
            return fail("Tiling needs at least two window names.")
        try:
            args.target, args.other = split_targets(str(args.op), list(args.targets), str(args.other))
        except RuntimeError as error:
            return fail(str(error))
        return emit(plan(str(args.op), args))
    except RuntimeError as error:
        return fail(str(error))
    except Exception as error:  # noqa: BLE001 - the tool reports failures as data
        return fail(f"Unexpected failure: {error}")


if __name__ == "__main__":
    sys.exit(main())
