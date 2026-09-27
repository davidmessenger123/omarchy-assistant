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

`install.sh` installs the project-local OpenCode tool dependency and adds the Hyprland keybinding for you. Image models are never downloaded automatically.

To install the local image model in the same step (about 13 GB, so it is opt-in and never happens by accident):

```bash
./install.sh --with-image-model
```

`install.sh --help` lists the options, including `--image-model-args "--rocm 6.4"` to pin a ROCm index and `--yes` to skip the confirmation prompt.

### Keybinding

By default `install.sh` binds `SUPER + SHIFT + Q` to launch the assistant, writing a clearly marked block into `~/.config/hypr/bindings.lua`:

```lua
-- >>> omarchy-assistant >>>
o.bind("SUPER + SHIFT + Q", "Omarchy Assistant", {
  launch = "/path/to/omarchy-assistant/run.sh"
})
-- <<< omarchy-assistant <<<
```

The installer is careful about your configuration:

- A timestamped backup is written to `bindings.lua.bak.<epoch>` before any change, and the file is replaced atomically.
- Re-running is safe. If the block already points at the right path it does nothing; if the repository moved, it updates the path in place.
- If something else is already bound to that combination, nothing is overwritten. The installer prints what the key was bound to and tells you to re-run with `--force-keybind`, which uses `o.rebind` and reports what it replaced.
- After writing, the config is validated with `hyprctl reload` and `hyprctl configerrors`. If Hyprland reports errors, the backup is restored automatically.
- Symlinked or read-only `bindings.lua` files, missing files, and non-Hyprland systems are skipped with the manual line to add.
- The launch path is only refreshed by re-running the installer; the **Update** button never edits your Hyprland config.

Options: `--keybind-combo "SUPER + ALT + G"`, `--force-keybind`, and `--no-keybind` to skip and print the line instead.

The installer reloads Hyprland for you, so the shortcut works right away. Launch with `run.sh` or the binding.

For synthetic clicking, ensure the user can access `/dev/uinput` using the system's udev/ACL policy. The assistant refuses to silently fall back to unrestricted input.

## Settings

Behaviour lives in `~/.config/omarchy-assistant/config.json`, created automatically the first time you change a setting in the app. Environment variables win over the file, and the built-in defaults win over nothing, so a one-off export always beats a saved preference.

```bash
./assistant_config.py --format json      # effective settings
./assistant_config.py keys               # every key, its env var, and default
./assistant_config.py get image_backend
./assistant_config.py set confirm_images true
./assistant_config.py set max_autonomy_steps 12
```

Keys cover the chat model and the optional escalate model, the image backend, both image model ids, the image directory, rate limits, timeouts, `confirm_images`, `clipboard_enabled`, `max_autonomy_steps`, `max_looks`, `screen_monitor`, `reminders_enabled`, `dictation_command`, and `keybind_combo`. A file that is not valid JSON is reported and ignored rather than breaking the app.

## Checking an installation

```bash
./bin/assistant-doctor            # add --quiet for only problems, --network to test reachability
```

It verifies required commands, `/dev/uinput` access, venv support, the settings file, the Hyprland config and keybinding, the git checkout and whether it is behind `origin`, the OpenCode install and model list, the Gemini key (without printing it), the local image backend including GPU runtime and cached weights, free disk space, and that screen capture actually works. It changes nothing and exits non-zero if any check fails.

## Seeing the screen and images

**The assistant can ask to look at your screen.** When it needs something it cannot infer, such as an error message or what a dialog says, it calls `look_at` and stops. The app captures the screen and sends it back with the next turn, so the answer is based on what is actually on screen. Because OpenCode runs one turn at a time, the screenshot always arrives in a follow-up message rather than mid-answer. The number of screenshots per request is capped by `max_looks` (default 3), and the status line shows when the app is looking.

The **Screen** button forces a screenshot for the next message, and the app attaches one automatically when a request obviously needs it.

**You can attach images.** The **Attach** button takes an image from your Wayland clipboard and shows it as a chip with a **Remove** button. Mentioning a path also works: if your message contains something like `/home/you/Pictures/photo.png`, the app verifies it is a readable image under `/home` and attaches it automatically. An attached image is the visual context for that turn, so no screenshot is taken as well.

**Generated images can be iterated on.** After the assistant generates an image, the next message you send attaches that image automatically, so "make it darker" or "add rain" works: the model sees what it made, changes it, and can tell you what changed between the two.

Images are only read from paths under your home directory, are limited to 32 MB, and must be a format the model can read. Nothing is copied anywhere else.

## Action history

The **History** button shows what the assistant actually did: clicks with their position and target, typing targets, launched applications, generated images, opened files, screen captures, setting changes, and task starts and stops. It is stored locally at:

```text
$XDG_STATE_HOME/omarchy-assistant/action-log.jsonl
```

The default is `~/.local/state/omarchy-assistant/action-log.jsonl`, written with mode `0600`, trimmed to the most recent entries, and never leaves the machine. **Typed text is never stored**, only the target and the character count, because password managers put secrets on the clipboard. **Clear** in the panel empties it.

## Confirming paid images

Set `confirm_images` and every billable Gemini call waits for you:

```bash
./assistant_config.py set confirm_images true
```

The desktop app then shows a confirmation bar naming the prompt, backend, aspect ratio, and resolution before anything is sent. Approving writes a single-use approval, the assistant re-runs the turn with exactly the approved parameters, and the tool consumes the approval when it makes the call. Changing any parameter, or asking again afterwards, requires a new confirmation. The local SDXL-Turbo backend is free and never asks. The status line shows **Image approval** while a decision is pending.

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

Runs SDXL-Turbo on the GPU through a private virtual environment, so nothing is installed system-wide:

```bash
./bin/assistant-config setup-image-models   # venv, PyTorch, diffusers, then the weights
./bin/assistant-config image-status         # GPU vendor, runtime, and cached-weight report
./bin/assistant-config image-setup-plan     # what would be installed, without installing
```

The first command creates `~/.local/share/omarchy-assistant/image-venv`, installs PyTorch and diffusers, and downloads the SDXL-Turbo weights (about 7 GB). SDXL-Turbo needs about 5 GB of VRAM; smaller cards work because the weights stream from system RAM, and the helper automatically stays on the CPU when no supported GPU is visible.

The setup command detects the GPU vendor and installs a matching PyTorch build: CUDA wheels from PyPI on NVIDIA, and ROCm wheels from `download.pytorch.org` on AMD. Check what it will do with `image-setup-plan` before installing, and override the detection when needed:

```bash
./bin/assistant-config setup-image-models --backend amd --rocm 6.4
./bin/assistant-config setup-image-models --rebuild-torch   # replace an existing PyTorch
```

`ASSISTANT_IMAGE_TORCH_BACKEND` (`nvidia`, `amd`, `intel`, `none`) and `ASSISTANT_ROCM_VERSION` do the same from the environment.

Measured on an RTX A3000 Laptop GPU with 6 GB of VRAM, using model offload:

| Size | Steps | Time |
| --- | --- | --- |
| 512x512 | 2 | ~6 s |
| 768x768 | 2 | ~8-9 s |
| 768x768 | 4 | ~10 s |
| 1024x576 (16:9) | 2 | ~10-11 s |

Each request also pays a one-off ~5 s model load, so a single image takes roughly 13-16 s end to end. Extra steps barely change the time, because streaming the weights dominates on a card this size. Quality is good for drafts, stylised art, and private subjects, and noticeably weaker than Gemini for photorealism, precise composition, and text inside the image. Use `local` for drafts, iterations, private subjects, and offline work; switch to `gemini` for the final image.

#### AMD GPUs (ROCm)

Local generation works on AMD cards that ROCm supports, and the helper detects the HIP runtime the same way it detects CUDA:

- Supported: Radeon RX 9000 (RDNA 4), Radeon RX 7000 and PRO W7000 (RDNA 3), Radeon PRO V (RDNA 2), and Ryzen AI 300/400 APUs. Older GCN and Vega cards are not supported by current ROCm.
- Install ROCm itself first. On Arch: `sudo pacman -S rocm rocm-hip rocm-smi-lib`. AMD officially supports Ubuntu, RHEL, and Windows, so other distributions may need extra work, and some Arch setups need `HSA_OVERRIDE_GFX_VERSION`.
- Then run `./bin/assistant-config setup-image-models`. It picks a ROCm PyTorch wheel index automatically, using the installed ROCm version when it can, and falls back to 6.4 otherwise.
- `image-status` reports `runtime=hip` with the HIP version, the device name, and the VRAM. If it reports `runtime=cpu`, the installed PyTorch has no GPU support, so rebuild it with `--rebuild-torch`.

Intel GPUs have no supported PyTorch path for this model here, so `setup-image-models` warns and local generation falls back to the CPU, which is very slow. Use the Gemini backend on Intel machines.

### Notes

- Every Gemini image is a billable API call, and the prompt is sent to Google. Never include secrets or private data in a Gemini image prompt.
- Generation is rate limited to 10 images per hour and 60 per day across both backends. Override with `ASSISTANT_IMAGE_HOURLY_LIMIT` and `ASSISTANT_IMAGE_DAILY_LIMIT`.
- `ASSISTANT_IMAGE_MODEL`, `ASSISTANT_GEMINI_ENDPOINT`, `ASSISTANT_IMAGE_DIR`, and `ASSISTANT_IMAGE_TIMEOUT_MS` tune the Gemini backend. `ASSISTANT_IMAGE_LOCAL_MODEL`, `ASSISTANT_IMAGE_PYTHON`, and `ASSISTANT_IMAGE_LOCAL_TIMEOUT_MS` tune the local backend, for example to point at a different Diffusers model or an API-compatible proxy.

## Updates

The **Update** button checks the current `origin` branch on GitHub without changing the checkout. If the branch is behind and the working tree is clean, **Update now** performs a fast-forward pull, refreshes the project-local OpenCode dependency, and restarts the assistant. Local uncommitted changes block applying an update; commit or stash them first.

Updates never download image models or the virtual environment, so an existing machine keeps its weights and never re-pulls 13 GB. To add the model to a machine that does not have it yet, run `bin/assistant-config setup-image-models` once.

### Troubleshooting

`bash: ./bin/assistant-config: No such file or directory` means the checkout on that machine predates the image-generation commits, or you are not in the repository root. Check with:

```bash
cd <path-to-omarchy-assistant>
git log --oneline -1      # want 54f81f3 or newer
git status --short        # any output means a dirty tree, which blocks Update
ls bin/                   # want assistant-config listed
```

If the tree is dirty, `git stash` or commit the changes, then click **Update** again or run `git pull --ff-only`.

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
