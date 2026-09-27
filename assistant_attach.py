#!/usr/bin/env python3
"""Attach images to an assistant turn.

Two jobs, both returning one JSON object on stdout:
  clipboard <target>   save an image from the Wayland clipboard to <target>
  resolve <path>       report whether <path> is an image that is safe to attach

Images are only ever read from a path the user already has, or from their own
clipboard, and the clipboard is never written anywhere except the given target.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 32 * 1024 * 1024
CLIPBOARD_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp")
ALLOWED_ROOTS = ("/home/",)


def fail(error: str, **extra) -> None:
    json.dump({"ok": False, "error": error, **extra}, sys.stdout)
    sys.stdout.write("\n")


def ok(**payload) -> None:
    json.dump({"ok": True, **payload}, sys.stdout)
    sys.stdout.write("\n")


def is_image(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or ""
    if mime in CLIPBOARD_TYPES:
        return mime
    # mimetypes does not always know these, so check the extension directly.
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(suffix, "")


def safe_path(candidate: str) -> Path | None:
    """Only allow images under the user's home, with no traversal tricks."""
    if not candidate:
        return None
    expanded = os.path.expanduser(candidate.strip())
    if not expanded.startswith("/"):
        expanded = str(Path.home() / expanded)
    try:
        resolved = Path(expanded).resolve()
    except OSError:
        return None
    if not str(resolved).startswith(ALLOWED_ROOTS):
        return None
    return resolved


def clipboard_image(target: str) -> None:
    if not shutil.which("wl-paste"):
        fail("wl-paste is not installed, so the clipboard cannot be read.")
        return
    try:
        listing = subprocess.run(
            ["wl-paste", "--list-types"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"Could not read the clipboard: {error}")
        return
    types = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    chosen = next((value for value in CLIPBOARD_TYPES if value in types), "")
    if not chosen:
        fail("There is no image on the clipboard. Copy an image first, then try again.", clipboard_types=types[:12])
        return

    destination = Path(target).expanduser()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            result = subprocess.run(
                ["wl-paste", "--type", chosen], stdout=handle, stderr=subprocess.PIPE, timeout=30, check=False
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"Could not save the clipboard image: {error}")
        return
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        fail("The clipboard image could not be written.", stderr=result.stderr.decode("utf-8", "replace")[:200])
        return
    if destination.stat().st_size > MAX_BYTES:
        destination.unlink(missing_ok=True)
        fail("The clipboard image is larger than 32 MB.")
        return
    ok(path=str(destination), mime=chosen, bytes=destination.stat().st_size, source="clipboard")


def resolve(candidate: str) -> None:
    path = safe_path(candidate)
    if path is None:
        fail("Only images under your home directory can be attached.")
        return
    if not path.is_file():
        fail("That file does not exist.", path=str(path))
        return
    mime = is_image(path)
    if not mime:
        fail("That file is not an image the model can read.", path=str(path))
        return
    size = path.stat().st_size
    if size > MAX_BYTES:
        fail("That image is larger than 32 MB.", path=str(path), bytes=size)
        return
    ok(path=str(path), mime=mime, bytes=size, source="file")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("clipboard", "resolve"))
    parser.add_argument("target", nargs="?")
    arguments = parser.parse_args()

    if not arguments.target:
        print(f"{arguments.command} needs a target", file=sys.stderr)
        return 1
    if arguments.command == "clipboard":
        clipboard_image(arguments.target)
    else:
        resolve(arguments.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
