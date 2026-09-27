# Omarchy Assistant

A Quickshell desktop assistant for Omarchy/Hyprland. It uses OpenCode as its model backend and supports:

- conversational chat with web search and read-only file search under `/home`
- installed-application lookup
- guarded computer control with click, typing, and application-launch actions
- iterative autonomous tasks that re-capture the screen after each action
- first-use approval for each click/typing target, plus confirmation for sensitive actions
- local memory for preferences, instructions, project context, personal facts, and conversation summaries

The assistant never edits files or runs arbitrary shell commands. Computer actions are executed by the local QML process only after the model proposes them and the safety checks pass.

## Requirements

- Arch/Omarchy with Hyprland and Quickshell
- `opencode` installed and authenticated
- `node`, `npm`, `git`, `python3`, `wtype`, `uwsm-app`, `hyprctl`, and `xdg-open`
- `/dev/uinput` access for synthetic mouse input

The repository includes an x86-64 capture helper at `bin/screen_capture-x86_64`. The `bin/screen_capture` wrapper uses it on x86-64 and falls back to the system `grim` on other CPU architectures. The rest of the project is source-level and portable.

## Install

```bash
git clone https://github.com/davidmessenger123/omarchy-assistant.git
cd omarchy-assistant
./install.sh
```

`install.sh` installs the project-local OpenCode tool dependency and prints the exact Hyprland binding to add. It does not modify Hyprland configuration automatically.

Add this to `~/.config/hypr/bindings.lua`, using the clone path printed by the installer:

```lua
o.bind("SUPER + SHIFT + Q", "Omarchy Assistant", {
  launch = "/path/to/omarchy-assistant/run.sh"
})
```

Reload Hyprland, then launch with `run.sh` or the binding.

For synthetic clicking, ensure the user can access `/dev/uinput` using the system's udev/ACL policy. The assistant refuses to silently fall back to unrestricted input.

## Updates

The **Update** button checks the current `origin` branch on GitHub without changing the checkout. If the branch is behind and the working tree is clean, **Update now** performs a fast-forward pull, refreshes the project-local OpenCode dependency, and restarts the assistant. Local uncommitted changes block applying an update; commit or stash them first.

## Memory

Memory is stored locally at:

```text
$XDG_STATE_HOME/omarchy-assistant/memory.json
```

The default is `~/.local/state/omarchy-assistant/memory.json`. The file is written with mode `0600`. Use the assistant's **Memory** button to view entries; **Clear all** requires a second click. Likely credentials and secrets are rejected rather than stored.

Conversation summaries are stored automatically. Durable preferences, instructions, project context, and personal facts are stored when the model identifies them as durable or when the user asks it to remember them. Recalled memory is passed to the model as untrusted background data, not as executable instructions.

## Autonomy and safety

The **Auto** control enables guarded autonomy. Low-risk application launches can run automatically. The first click or typing target in a task requires approval, and each distinct target is displayed before execution. Destructive, credential, financial, communicative, and other sensitive actions always require confirmation.

While an autonomous task is running:

- the assistant hides its overlay before acting;
- the focused monitor is captured again after each action;
- the task is limited to eight actions;
- `Ctrl+.` stops the task;
- `Esc` closes the assistant.

On multi-monitor Hyprland sessions, the capture helper temporarily disables non-focused outputs because the current Hyprland screencopy implementation can stall when multiple outputs are active. The outputs are restored automatically, and captured files are mode `0600`.

## Development checks

```bash
python3 -m py_compile assistant_memory.py screen_click.py
/usr/lib/qt6/bin/qmllint -I /usr/lib/qt6/qml shell.qml
quickshell --no-duplicate --path shell.qml
```

The project-local OpenCode tools are in `.opencode/tools/`; the agent policy is `.opencode/agents/chatbot.md`.
