import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"
import { promisify } from "node:util"

const run = promisify(execFile)
const appDir = process.env.ASSISTANT_APP_DIR || process.cwd()
const helper = join(appDir, "assistant_notifications.py")
const configPath = process.env.ASSISTANT_CONFIG || join(process.env.XDG_CONFIG_HOME || join(homedir(), ".config"), "omarchy-assistant", "config.json")

type Settings = Record<string, unknown>
let cached: Settings | null = null

async function loadSettings(): Promise<Settings> {
    if (cached) return cached
    cached = {}
    try {
        const parsed = JSON.parse(await readFile(configPath, "utf8"))
        if (parsed && typeof parsed === "object") cached = parsed
    } catch {}
    return cached
}

function flag(settings: Settings, envName: string, key: string, fallback = false) {
    const fromEnv = process.env[envName]
    if (fromEnv !== undefined && String(fromEnv) !== "") {
        const value = String(fromEnv).trim().toLowerCase()
        if (["1", "true", "yes", "on"].includes(value)) return true
        if (["0", "false", "no", "off"].includes(value)) return false
    }
    const fromFile = settings[key]
    if (fromFile !== undefined && fromFile !== null && String(fromFile) !== "") {
        return ["1", "true", "yes", "on"].includes(String(fromFile).trim().toLowerCase())
    }
    return fallback
}

const ENABLE_HINT = "Notification history is turned off. The user can enable it with ./assistant_config.py set notifications true, or by exporting ASSISTANT_NOTIFICATIONS=1."

async function helperCall(args: string[]) {
    try {
        const done = await run("/usr/bin/python3", [helper, ...args], { timeout: 20000, maxBuffer: 1024 * 1024 })
        const parsed = JSON.parse(String(done.stdout || "").trim())
        if (!parsed || parsed.ok !== true) {
            return { ok: false as const, error: String(parsed?.error || "the notification history could not be read") }
        }
        return { ok: true as const, data: parsed }
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        if (/ENOENT/.test(message)) {
            return { ok: false as const, error: "the notification history helper is missing from this installation" }
        }
        return { ok: false as const, error: `the notification history could not be read: ${message.slice(0, 160)}` }
    }
}

export default tool({
    description: "Answer what notifications the user missed, and manage a quiet stretch. Use it for 'what did I miss', 'catch me up', 'anything while I was away', or to start and end a focus stretch. Recording is off by default because notification bodies carry message previews. Note that Omarchy's shell owns notifications and exposes no do-not-disturb control, so a focus stretch is recorded rather than silencing alerts; the user silences those in the shell's own menu. Summarise what arrived in plain language rather than dumping bodies, and never store notification contents in memory.",
    args: {
        action: tool.schema.string().describe("One of: catchup, list, status, read, clear, focus_start, focus_stop, focus_status, dismiss."),
        minutes: tool.schema.number().describe("How many minutes back to look, or how long a focus stretch runs."),
        limit: tool.schema.number().describe("How many notifications to return. Defaults to 20.")
    },
    async execute(args) {
        const settings = await loadSettings()
        const action = String(args.action || "catchup").trim().toLowerCase()
        const allowed = ["catchup", "list", "status", "read", "clear", "focus_start", "focus_stop", "focus_status", "dismiss"]
        if (!allowed.includes(action)) {
            return JSON.stringify({ ok: false, error: `Choose one of: ${allowed.join(", ")}.` })
        }
        if (!flag(settings, "ASSISTANT_NOTIFICATIONS", "notifications")) {
            return JSON.stringify({ ok: false, action: "notifications", error: ENABLE_HINT, configured: false })
        }

        const minutes = Math.round(Number(args.minutes))
        const limit = Number.isFinite(Number(args.limit)) ? Math.max(1, Math.min(Math.round(Number(args.limit)), 50)) : 20

        if (action === "dismiss") {
            // Clearing the notification centre throws notifications away, so it is
            // proposed for approval rather than done behind the user's back.
            return JSON.stringify({
                ok: true,
                action: "command",
                command: "dismiss_notifications",
                summary: "Clear the notification centre",
                detail: "every notification on screen is discarded"
            })
        }
        if (action === "focus_start") {
            if (!Number.isFinite(minutes) || minutes < 1) {
                return JSON.stringify({ ok: false, error: "Say how many minutes the focus stretch should last." })
            }
            const started = await helperCall(["focus-start", String(Math.min(minutes, 1440))])
            return JSON.stringify({ ok: started.ok, action: "notifications", op: action, note: "Omarchy cannot silence notifications; this records the stretch so catch-up can cover it.", ...(started.ok ? { data: started.data } : { error: started.error }) })
        }
        if (action === "focus_stop") {
            const stopped = await helperCall(["focus-stop"])
            return JSON.stringify({ ok: stopped.ok, action: "notifications", op: action, ...(stopped.ok ? { data: stopped.data } : { error: stopped.error }) })
        }
        if (action === "clear") {
            const cleared = await helperCall(["clear"])
            return JSON.stringify({ ok: cleared.ok, action: "notifications", op: action, cleared: true, error: cleared.ok ? "" : cleared.error })
        }
        if (action === "read") {
            const marked = await helperCall(["read"])
            return JSON.stringify({ ok: marked.ok, action: "notifications", op: action, ...(marked.ok ? { data: marked.data } : { error: marked.error }) })
        }
        if (action === "status" || action === "focus_status") {
            const command = action === "status" ? "status" : "focus-status"
            const state = await helperCall([command])
            return JSON.stringify({ ok: state.ok, action: "notifications", op: action, ...(state.ok ? { data: state.data } : { error: state.error }) })
        }

        // With no minutes given, catchup follows the current focus stretch and
        // list returns everything recent. Either accepts an explicit look-back.
        const argsList = ["list", "--limit", String(limit)]
        if (Number.isFinite(minutes) && minutes > 0) argsList.push("--since", String(Math.min(minutes, 1440)))
        const listed = await helperCall(argsList)
        if (!listed.ok) return JSON.stringify({ ok: false, action: "notifications", op: action, error: listed.error })
        const data = listed.data as { entries?: unknown[]; count?: number; recording?: boolean; since_minutes?: number }
        return JSON.stringify({
            ok: true,
            action: "notifications",
            op: action,
            recording: data.recording === true,
            window_minutes: Number(data.since_minutes) || 0,
            count: Number(data.count) || 0,
            entries: data.entries || []
        })
    }
})
