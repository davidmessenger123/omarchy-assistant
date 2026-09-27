import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { join } from "node:path"
import { promisify } from "node:util"

const run = promisify(execFile)
const appDir = process.env.ASSISTANT_APP_DIR || process.cwd()
const helper = join(appDir, "assistant_windows.py")

const OPERATIONS = ["list", "focus", "move", "swap", "resize", "float", "fullscreen", "workspace", "monitor", "scratchpad", "tile"]

async function listWindows() {
    try {
        const done = await run("/usr/bin/python3", [helper, "plan", "list"], { timeout: 20000, maxBuffer: 512 * 1024 })
        const parsed = JSON.parse(String(done.stdout || "").trim())
        if (!parsed || parsed.ok !== true) return JSON.stringify({ ok: false, error: String(parsed?.error || "the window list could not be read") })
        const windows = (parsed.windows || []).map((w: Record<string, unknown>) => ({
            class: String(w.class || ""),
            title: String(w.title || ""),
            workspace: String(w.workspace || ""),
            monitor: w.monitor,
            at: w.at,
            size: w.size,
            floating: w.floating === true
        }))
        return JSON.stringify({ ok: true, action: "window_list", count: windows.length, windows })
    } catch (error) {
        return JSON.stringify({ ok: false, error: `The window list could not be read: ${error instanceof Error ? error.message : String(error)}` })
    }
}

export default tool({
    description: "Control the user's windows and workspaces in Hyprland. Use it for requests like 'what's open right now', 'focus the browser', 'move Teams to the big monitor', 'put these two windows side by side', 'switch to workspace 3', 'fullscreen this', or 'send it to the scratchpad'. Listing windows only reads state and answers straight away. Every change is only a request: the desktop app shows the user a card, nothing moves until they approve it, and you get no result telling you it happened. So never write that a window has been moved, resized, tiled, focused, or is done, and never say 'Done'. Say instead that you have asked for it and it is waiting for approval. There is deliberately no way to close or kill a window through this tool, so ask the user to do that themselves.",
    args: {
        op: tool.schema.string().describe("One of: list, focus, move, swap, resize, float, fullscreen, workspace, monitor, scratchpad, tile."),
        target: tool.schema.string().describe("Which window: a class such as foot, a piece of its title, or an address such as 0x55e0ed736b60. Leave empty for list, workspace, monitor, and a bare scratchpad toggle."),
        to: tool.schema.string().describe("Destination for move: a workspace number or name, or monitor:left, monitor:right, monitor:DP-1. Also used for resize, float and fullscreen to pass enable, disable, or toggle."),
        other: tool.schema.string().describe("The second window for swap."),
        targets: tool.schema.string().describe("Two or more window names for tile, separated by commas."),
        width: tool.schema.number().describe("Optional new width in pixels for resize."),
        height: tool.schema.number().describe("Optional new height in pixels for resize."),
        direction: tool.schema.string().describe("For tile: left or right, meaning the order the windows are placed in. Defaults to left.")
    },
    async execute(args) {
        const op = String(args.op || "").trim().toLowerCase()
        if (!OPERATIONS.includes(op)) {
            return JSON.stringify({ ok: false, error: `Choose one of: ${OPERATIONS.join(", ")}.` })
        }
        if (op === "list") return listWindows()

        const target = String(args.target || "").trim().slice(0, 200)
        const other = String(args.other || "").trim().slice(0, 200)
        const targets = String(args.targets || "").trim().slice(0, 600)
        const to = String(args.to || "").trim().slice(0, 120)

        if (op === "list") {
            return JSON.stringify({ ok: true, action: "window", op, target: "open windows", query: true })
        }
        if (op === "tile") {
            const names = targets.split(",").map((part) => part.trim()).filter(Boolean)
            if (names.length < 2) {
                return JSON.stringify({ ok: false, error: "Tiling needs at least two windows, for example targets: 'foot,brave'." })
            }
            return JSON.stringify({
                ok: true,
                action: "window",
                op,
                target: names.join(","),
                direction: String(args.direction || "left").trim().toLowerCase() === "right" ? "right" : "left",
                detail: "floats them and places them in equal columns; the user can unfloat afterwards",
                applied: false,
                state: "waiting_for_approval"
            })
        }
        if (op === "swap" && (!target || !other)) {
            return JSON.stringify({ ok: false, error: "Swapping needs two windows: set target and other." })
        }
        if (["workspace", "monitor"].includes(op) && !to) {
            return JSON.stringify({
                ok: false,
                error: op === "monitor"
                    ? "Say which monitor to switch to: monitor:left, monitor:right, or a name such as DP-1."
                    : "Say which workspace to switch to: a number such as 3, a name, or next."
            })
        }
        if (!["workspace", "monitor"].includes(op) && !to && !["focus", "swap", "fullscreen", "scratchpad"].includes(op) && !target) {
            return JSON.stringify({ ok: false, error: "Say which window to work on." })
        }
        if (op === "resize") {
            const width = Number(args.width)
            const height = Number(args.height)
            if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
                return JSON.stringify({ ok: false, error: "Resizing needs a width and a height in pixels." })
            }
        }

        return JSON.stringify({
            ok: true,
            action: "window",
            op,
            target,
            to,
            other,
            width: Number(args.width) || 0,
            height: Number(args.height) || 0,
            direction: String(args.direction || "left").trim().toLowerCase() === "right" ? "right" : "left",
            applied: false,
            state: "waiting_for_approval",
            note: "Requested only. The user must approve the card before anything changes, and nothing in this result says it happened."
        })
    }
})
