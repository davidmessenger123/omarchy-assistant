import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"
import { promisify } from "node:util"

const run = promisify(execFile)
const appDir = process.env.ASSISTANT_APP_DIR || process.cwd()
const helper = join(appDir, "assistant_clipboard_history.py")
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

const ENABLE_HINT = "Clipboard history is turned off. The user can enable it with ./assistant_config.py set clipboard_history true, or by exporting ASSISTANT_CLIPBOARD_HISTORY=1."

function helperError(error: unknown) {
        const message = error instanceof Error ? error.message : String(error)
        const stdout = (error as { stdout?: unknown })?.stdout
        if (typeof stdout === "string" && stdout.trim().startsWith("{")) {
            try {
                return JSON.parse(stdout.trim())
            } catch {}
        }
        return null
    }

async function helperCall(args: string[]) {
    // A helper that crashes must read as a clear message, not an exception the
    // model has to make sense of.
    try {
        const done = await run("/usr/bin/python3", [helper, ...args], { timeout: 20000, maxBuffer: 1024 * 1024 })
        const parsed = JSON.parse(String(done.stdout || "").trim())
        if (!parsed || parsed.ok !== true) {
            return { ok: false as const, error: String(parsed?.error || "the clipboard history could not be read") }
        }
        return { ok: true as const, data: parsed }
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        if (/ENOENT/.test(message)) {
            return { ok: false as const, error: "the clipboard history helper is missing from this installation" }
        }
        const said = helperError(error)
        if (said) return { ok: false as const, error: String(said.error || "the helper reported a problem"), problems: said.problems || [], data: said }
        return { ok: false as const, error: `the clipboard history could not be read: ${message.slice(0, 160)}` }
    }
}

export default tool({
    description: "Search what the user copied earlier, and paste an item back into the field they are in. Use it for 'what did I copy a minute ago', 'find that link I copied', or 'paste the third one back'. It only works when the user has turned clipboard history on, which is off by default because the clipboard is where password managers keep secrets. Never store history contents in memory, never repeat a secret in full, and never put one into an image prompt.",
    args: {
        action: tool.schema.string().describe("One of: list, get, paste, status, clear."),
        number: tool.schema.number().describe("Which entry, counting from 1 with the most recent first. Needed for get and paste."),
        query: tool.schema.string().describe("Optional text to search the previews for, when listing."),
        limit: tool.schema.number().describe("How many entries to list. Defaults to 15.")
    },
    async execute(args) {
        const settings = await loadSettings()
        const action = String(args.action || "list").trim().toLowerCase()
        if (!["list", "get", "paste", "status", "clear"].includes(action)) {
            return JSON.stringify({ ok: false, error: "Choose one of: list, get, paste, status, clear." })
        }
        if (!flag(settings, "ASSISTANT_CLIPBOARD_HISTORY", "clipboard_history")) {
            return JSON.stringify({ ok: false, action: "clipboard_history", error: ENABLE_HINT, configured: false })
        }

        const number = Math.round(Number(args.number))
        const limit = Number.isFinite(Number(args.limit)) ? Math.max(1, Math.min(Math.round(Number(args.limit)), 50)) : 15
        const query = String(args.query || "").trim().slice(0, 120)

        if (action === "clear") {
            const cleared = await helperCall(["clear"])
            return JSON.stringify({ ok: cleared.ok, action: "clipboard_history", cleared: true, error: cleared.ok ? "" : cleared.error })
        }

        if (action === "get" || action === "paste") {
            if (!Number.isFinite(number) || number < 1) {
                return JSON.stringify({ ok: false, error: "Say which entry to use, counting from 1 with the most recent first." })
            }
            const found = await helperCall(["get", String(number)])
            if (!found.ok) return JSON.stringify({ ok: false, action: "clipboard_history", error: found.error })
            const entry = found.data as { text?: string; chars?: number; truncated?: boolean }
            if (action === "get") {
                return JSON.stringify({ ok: true, action: "clipboard_history", number, text: String(entry.text || ""), chars: Number(entry.chars) || 0, truncated: entry.truncated === true })
            }
            // Pasting reuses the ordinary typing approval path, so the user still
            // sees what is about to be typed and the text is never written to the log.
            return JSON.stringify({
                ok: true,
                action: "type",
                text: String(entry.text || ""),
                target: `clipboard entry ${number}`,
                enter: false,
                source: "type_text",
                note: `clipboard entry ${number}, ${Number(entry.chars) || 0} characters`
            })
        }

        const listed = await helperCall(["list", "--limit", String(limit), ...(query ? ["--query", query] : [])])
        if (!listed.ok) return JSON.stringify({ ok: false, action: "clipboard_history", error: listed.error })
        const data = listed.data as { entries?: unknown[]; count?: number; recording?: boolean }
        return JSON.stringify({
            ok: true,
            action: "clipboard_history",
            op: action,
            recording: data.recording === true,
            count: Number(data.count) || 0,
            entries: data.entries || []
        })
    }
})
