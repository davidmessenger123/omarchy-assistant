#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys


def run(command, cwd, timeout=30):
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(command, cwd=cwd, env=environment, text=True, capture_output=True, timeout=timeout)


def result_error(message, **extra):
    payload = {"ok": False, "error": str(message)[:1000]}
    payload.update(extra)
    return payload


def repository_state(app_dir, branch):
    head = run(["git", "rev-parse", "HEAD"], app_dir)
    if head.returncode != 0:
        raise RuntimeError(head.stderr.strip() or "This installation is not a Git checkout.")
    remote = run(["git", "remote", "get-url", "origin"], app_dir)
    if remote.returncode != 0:
        raise RuntimeError("This installation has no origin remote.")
    status = run(["git", "status", "--porcelain"], app_dir)
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "Could not inspect the Git working tree.")
    latest = run(["git", "ls-remote", "origin", f"refs/heads/{branch}"], app_dir, timeout=20)
    if latest.returncode != 0:
        raise RuntimeError(latest.stderr.strip() or "Could not reach GitHub.")
    latest_sha = latest.stdout.strip().split()[0] if latest.stdout.strip() else ""
    if not latest_sha:
        raise RuntimeError(f"GitHub did not return refs/heads/{branch}.")
    return {
        "current": head.stdout.strip(),
        "latest": latest_sha,
        "remote": remote.stdout.strip(),
        "branch": branch,
        "dirty": bool(status.stdout.strip()),
    }


def current_branch(app_dir):
    branch = run(["git", "symbolic-ref", "--short", "HEAD"], app_dir)
    return branch.stdout.strip() if branch.returncode == 0 and branch.stdout.strip() else "main"


def check(app_dir):
    branch = current_branch(app_dir)
    state = repository_state(app_dir, branch)
    state.update({"ok": True, "updateAvailable": state["current"] != state["latest"], "dirty": state["dirty"]})
    return state


def update(app_dir):
    branch = current_branch(app_dir)
    state = repository_state(app_dir, branch)
    if state["dirty"]:
        return result_error("The local checkout has uncommitted changes; commit or stash them before updating.", current=state["current"], latest=state["latest"])
    pull = run(["git", "pull", "--ff-only", "origin", branch], app_dir, timeout=120)
    if pull.returncode != 0:
        return result_error(pull.stderr.strip() or pull.stdout.strip() or "git pull failed.")
    npm = shutil.which("npm")
    if npm:
        install = run([npm, "install", "--prefix", os.path.join(app_dir, ".opencode")], app_dir, timeout=120)
        if install.returncode != 0:
            return result_error(install.stderr.strip() or install.stdout.strip() or "npm install failed.")
    head = run(["git", "rev-parse", "HEAD"], app_dir)
    return {"ok": True, "updated": head.stdout.strip() != state["current"], "current": head.stdout.strip(), "previous": state["current"], "branch": branch, "remote": state["remote"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "update"))
    parser.add_argument("--dir", dest="app_dir", required=True)
    args = parser.parse_args()
    app_dir = os.path.abspath(args.app_dir)
    try:
        payload = check(app_dir) if args.action == "check" else update(app_dir)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
        payload = result_error(error)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
