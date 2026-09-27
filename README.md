# Omarchy Assistant

A Quickshell desktop assistant for Omarchy/Hyprland. It uses OpenCode as its model backend and supports:

- conversational chat with web search and read-only file search under `/home`
- installed-application lookup
- guarded computer control with click, typing, and application-launch actions
- iterative autonomous tasks that re-capture the screen after each action
- first-use approval for each click/typing target, plus confirmation for sensitive actions
- local memory for preferences, instructions, project context, personal facts, and conversation summaries
- image generation through Gemini and/or a local GPU model, with inline preview and one-click opening

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

## Image generation

The chat model (Space Bunny) cannot create images, so the Assistant calls a dedicated `generate_image` tool. Ask for an image in plain language, for example "draw a watercolour of a lighthouse at dusk in 16:9". The Assistant picks the aspect ratio, saves the file under `~/Pictures/omarchy-assistant/`, and shows the image inline with an **Open** button. This version generates new images only; it cannot edit an existing image.

Two backends are available, and the header button cycles between them (`Img: Gemini`, `Img: Local`, `Img: Auto`) while `ASSISTANT_IMAGE_BACKEND` sets the default:

| Backend | Model | Cost | Privacy | Quality | Size |
| --- | --- | --- | --- | --- | --- |
| `gemini` | `gemini-3-pro-image` (Nano Banana Pro) | billed per image by Google | prompt sent to Google | best, strong text-in-image | up to 4K |
| `local` | `stabilityai/sdxl-turbo` on your GPU | free | prompt never leaves the machine | good for drafts and stylised art | up to ~1024 px |

### Gemini backend

Needs your own Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey):

```bash
printf '%s' 'your-gemini-api-key' | ./bin/assistant-config set-key
./bin/assistant-config status
```

The key is stored at `~/.config/omarchy-assistant/gemini.key` with mode `600`, is never written to the repository, and is never printed back. `GEMINI_API_KEY` in the environment works too and takes precedence. Use `./bin/assistant-config clear` to remove the stored key.

### Local backend

Runs SDXL-Turbo on the NVIDIA GPU through a private virtual environment, so nothing is installed system-wide:

```bash
./bin/assistant-config setup-image-models   # venv, PyTorch, diffusers, then the weights
./bin/assistant-config image-status          # GPU, torch, and cached-weight report
```

The first command creates `~/.local/share/omarchy-assistant/image-venv`, installs PyTorch and diffusers, and downloads the SDXL-Turbo weights (about 7 GB). The first generation also pays a one-time model load of several seconds. SDXL-Turbo needs about 6 GB of VRAM; smaller cards work because the weights stream from system RAM, and the helper automatically stays on the CPU when no CUDA GPU is visible.

On a 6 GB laptop GPU expect roughly 1-3 seconds per 1024-pixel image after the model is loaded, and noticeably weaker results than Gemini for photorealism, precise composition, and any text inside the image. Use `local` for drafts, iterations, private subjects, and offline work; switch to `gemini` for the final image.

### Notes

- Every Gemini image is a billable API call, and the prompt is sent to Google. Never include secrets or private data in a Gemini image prompt.
- Generation is rate limited to 10 images per hour and 60 per day across both backends. Override with `ASSISTANT_IMAGE_HOURLY_LIMIT` and `ASSISTANT_IMAGE_DAILY_LIMIT`.
- `ASSISTANT_IMAGE_MODEL`, `ASSISTANT_GEMINI_ENDPOINT`, `ASSISTANT_IMAGE_DIR`, and `ASSISTANT_IMAGE_TIMEOUT_MS` tune the Gemini backend. `ASSISTANT_IMAGE_LOCAL_MODEL`, `ASSISTANT_IMAGE_PYTHON`, and `ASSISTANT_IMAGE_LOCAL_TIMEOUT_MS` tune the local backend, for example to point at a different Diffusers model or an API-compatible proxy.

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
