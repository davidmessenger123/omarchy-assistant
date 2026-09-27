import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { mkdir, readFile, writeFile } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { promisify } from "node:util"

const run = promisify(execFile)

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

function setting(settings: Settings, envName: string, key: string, fallback: unknown) {
    const fromEnv = process.env[envName]
    if (fromEnv !== undefined && String(fromEnv) !== "") return fromEnv
    const fromFile = settings[key]
    if (fromFile !== undefined && fromFile !== null && String(fromFile) !== "") return fromFile
    return fallback
}

function flag(settings: Settings, envName: string, key: string, fallback = false) {
    const value = setting(settings, envName, key, fallback)
    if (typeof value === "boolean") return value
    return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase())
}

function stateDir() {
    return join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "omarchy-assistant")
}

const HOURLY_LIMIT = 6

async function withinLimit() {
    const file = join(stateDir(), "reminder-usage.json")
    const now = Date.now()
    let stamps: number[] = []
    try {
        const parsed = JSON.parse(await readFile(file, "utf8"))
        if (Array.isArray(parsed?.stamps)) stamps = parsed.stamps.map(Number).filter((value: unknown) => Number.isFinite(value))
    } catch {}
    const recent = stamps.filter((value) => now - value < 60 * 60 * 1000)
    if (recent.length >= HOURLY_LIMIT) return `${HOURLY_LIMIT} reminders were already set in the last hour. Wait a little before asking for more.`
    recent.push(now)
    try {
        await mkdir(dirname(file), { recursive: true, mode: 0o700 })
        await writeFile(file, `${JSON.stringify({ stamps: recent })}\n`, { mode: 0o600 })
    } catch {}
    return ""
}

export default tool({
    description: "Set a lightweight desktop notification reminder with Omarchy, or list and clear the ones already pending. Use this when the user asks to be reminded, pinged, or nudged in a number of minutes, such as 'remind me in 20 minutes to check the oven'. The reminder survives the assistant closing and fires as a desktop notification. Set action show to list pending reminders, or clear to remove them.",
    args: {
        action: tool.schema.string().describe("set (default), show, or clear."),
        minutes: tool.schema.number().describe("For action set: how many minutes from now, from 1 to 1440."),
        message: tool.schema.string().describe("For action set: what to remind the user about, in a few words.")
    },
    async execute(args) {
        const settings = await loadSettings()
        const action = String(args.action || "set").trim().toLowerCase()
        if (!["set", "show", "clear"].includes(action)) return JSON.stringify({ ok: false, action: "reminder", error: "Use action set, show, or clear." })
        if (!flag(settings, "ASSISTANT_REMINDERS", "reminders_enabled", true)) {
            return JSON.stringify({
                ok: false,
                action: "reminder",
                error: "Reminders are turned off. The user can enable them with ./assistant_config.py set reminders_enabled true.",
                configured: false
            })
        }

        try {
            if (action === "show") {
                const result = await run("omarchy", ["reminder", "show", "--json"], { timeout: 20000, maxBuffer: 256 * 1024 })
                return JSON.stringify({ ok: true, action: "reminder", listed: true, raw: String(result.stdout || "").trim().slice(0, 2000) })
            }
            if (action === "clear") {
                await run("omarchy", ["reminder", "clear"], { timeout: 20000 })
                return JSON.stringify({ ok: true, action: "reminder", cleared: true })
            }
        } catch (error) {
            return JSON.stringify({
                ok: false,
                action: "reminder",
                error: `The reminder command failed: ${error instanceof Error ? error.message : String(error)}`
            })
        }

        const minutes = Math.round(Number(args.minutes))
        const message = String(args.message || "").trim().slice(0, 200)
        if (!Number.isFinite(minutes) || minutes < 1 || minutes > 1440) {
            return JSON.stringify({ ok: false, action: "reminder", error: "Give a number of minutes from 1 to 1440." })
        }
        if (message.length < 2) return JSON.stringify({ ok: false, action: "reminder", error: "Say what to remind the user about." })

        const limited = await withinLimit()
        if (limited) return JSON.stringify({ ok: false, action: "reminder", error: limited })

        try {
            await run("omarchy", ["reminder", String(minutes), message], { timeout: 20000 })
        } catch (error) {
            return JSON.stringify({
                ok: false,
                action: "reminder",
                error: `The reminder could not be set: ${error instanceof Error ? error.message : String(error)}`
            })
        }
        return JSON.stringify({ ok: true, action: "reminder", minutes, message, fires_in_minutes: minutes })
    }
})
