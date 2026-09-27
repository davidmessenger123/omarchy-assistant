import { tool } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"
import { access, chmod, copyFile, mkdir, readFile, rename, stat, unlink, writeFile } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, extname, join } from "node:path"

// Edits an existing image from a text instruction. The gemini backend sends the
// image as a reference and is billed like any other Gemini call; the local
// backend runs SDXL-Turbo img2img on the user's GPU and is free.

const configPath = process.env.ASSISTANT_CONFIG || join(process.env.XDG_CONFIG_HOME || join(homedir(), ".config"), "omarchy-assistant", "config.json")
type Settings = Record<string, unknown>
let cachedSettings: Settings | null = null

async function loadSettings(): Promise<Settings> {
    if (cachedSettings) return cachedSettings
    cachedSettings = {}
    try {
        const parsed = JSON.parse(await readFile(configPath, "utf8"))
        if (parsed && typeof parsed === "object") cachedSettings = parsed
    } catch {}
    return cachedSettings
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

function approvalPath() {
    return join(stateDir(), "image-approval.json")
}

async function consumeApproval(request: Record<string, unknown>) {
    let approval: any = null
    try {
        approval = JSON.parse(await readFile(approvalPath(), "utf8"))
    } catch {
        return false
    }
    if (!approval || typeof approval !== "object") return false
    const matches = Object.keys(request).every((key) => String(approval[key] ?? "") === String(request[key] ?? ""))
    if (!matches) return false
    await unlink(approvalPath()).catch(() => {})
    return true
}

async function resolveKey() {
    const file = join(homedir(), ".config", "omarchy-assistant", "gemini.key")
    const fromEnv = String(process.env.GEMINI_API_KEY ?? "").split(/\r?\n/)[0].trim()
    if (fromEnv.length >= 20) return fromEnv
    try {
        const stored = (await readFile(file, "utf8")).split(/\r?\n/)[0].trim()
        if (stored.length >= 20) return stored
    } catch {}
    return ""
}

function slug(value: string) {
    return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "image"
}

function stamp() {
    const now = new Date()
    const pad = (value: number) => String(value).padStart(2, "0")
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

async function outputDir(settings: Settings) {
    const override = String(setting(settings, "ASSISTANT_IMAGE_DIR", "image_dir", "")).trim()
    if (override) return override.startsWith("$HOME") ? override.replace("$HOME", homedir()) : override
    const configured = String(process.env.XDG_PICTURES_DIR ?? "").trim()
    if (configured) return join(configured.replace("$HOME", homedir()), "omarchy-assistant")
    try {
        const text = await readFile(join(homedir(), ".config", "user-dirs.dirs"), "utf8")
        const match = text.match(/XDG_PICTURES_DIR\s*=\s*"?([^"\n]+)"?/)
        if (match) return join(match[1].trim().replace("$HOME", homedir()), "omarchy-assistant")
    } catch {}
    return join(homedir(), "Pictures", "omarchy-assistant")
}

async function recordAttempt(settings: Settings) {
    const hourly = Number(setting(settings, "ASSISTANT_IMAGE_HOURLY_LIMIT", "image_hourly_limit", 10)) || 10
    const daily = Number(setting(settings, "ASSISTANT_IMAGE_DAILY_LIMIT", "image_daily_limit", 60)) || 60
    const file = join(stateDir(), "image-usage.json")
    const now = Date.now()
    let stamps: number[] = []
    try {
        const parsed = JSON.parse(await readFile(file, "utf8"))
        if (Array.isArray(parsed?.stamps)) stamps = parsed.stamps.map(Number).filter((value: unknown) => Number.isFinite(value))
    } catch {}
    stamps = stamps.filter((value) => now - value < 24 * 60 * 60 * 1000)
    if (stamps.filter((value) => now - value < 60 * 60 * 1000).length >= hourly) {
        return `Hourly image limit reached (${hourly}). Wait, or raise image_hourly_limit.`
    }
    if (stamps.length >= daily) return `Daily image limit reached (${daily}). Wait until tomorrow, or raise image_daily_limit.`
    stamps.push(now)
    try {
        await mkdir(dirname(file), { recursive: true, mode: 0o700 })
        await writeFile(file, `${JSON.stringify({ stamps })}\n`, { mode: 0o600 })
    } catch {}
    return ""
}

function failure(error: string, extra: Record<string, unknown> = {}) {
    return JSON.stringify({ ok: false, action: "edit_image", error, ...extra })
}

function localPython(settings: Settings) {
    return String(
        setting(settings, "ASSISTANT_IMAGE_PYTHON", "", process.env.ASSISTANT_IMAGE_PYTHON || join(homedir(), ".local", "share", "omarchy-assistant", "image-venv", "bin", "python"))
    )
}

const extensions: Record<string, string> = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp" }

async function saveFrom(source: string, destination: string) {
    await mkdir(dirname(destination), { recursive: true, mode: 0o755 })
    try {
        await rename(source, destination)
    } catch {
        await copyFile(source, destination)
        await unlink(source).catch(() => {})
    }
    await chmod(destination, 0o644).catch(() => {})
}

function runLocal(python: string, request: Record<string, unknown>, timeoutMs: number) {
    return new Promise<{ stdout: string; stderr: string; error?: string }>((resolve) => {
        const helper = join(process.env.ASSISTANT_APP_DIR || process.cwd(), "assistant_image_local.py")
        const child = spawn(python, [helper, "edit"], { stdio: ["pipe", "pipe", "pipe"] })
        let stdout = ""
        let stderr = ""
        let settled = false
        const finish = (value: { stdout: string; stderr: string; error?: string }) => {
            if (settled) return
            settled = true
            clearTimeout(timer)
            resolve(value)
        }
        const timer = setTimeout(() => {
            child.kill("SIGKILL")
            finish({ stdout, stderr, error: `Local editing exceeded ${Math.round(timeoutMs / 1000)} seconds.` })
        }, timeoutMs)
        child.stdout.on("data", (chunk) => (stdout += chunk))
        child.stderr.on("data", (chunk) => {
            stderr = (stderr + chunk).slice(-2000)
        })
        child.on("error", (error) => finish({ stdout, stderr, error: error instanceof Error ? error.message : String(error) }))
        child.on("close", () => finish({ stdout, stderr }))
        child.stdin.on("error", () => {})
        child.stdin.end(JSON.stringify(request))
    })
}

export default tool({
    description: "Edit an existing image from a text instruction and save the result under the user's Pictures directory. 'gemini' sends the image as a reference to Google's Gemini image model, which follows instructions closely and is billed per call; 'local' runs SDXL-Turbo img2img on the user's GPU, which is free and private but only nudges the original, so use a short instruction like 'make it snowy' rather than a full redraw. Use this when the user wants a change to an image they already have. Always pass the real path of the image. The tool returns the saved absolute path.",
    args: {
        path: tool.schema.string().describe("Absolute path of the image to edit, under /home."),
        instruction: tool.schema.string().describe("What to change, such as 'make it winter with snow falling' or 'replace the sky with a clear blue one'."),
        backend: tool.schema.string().describe("Backend: gemini, local, or auto. Defaults to the user's configured preference."),
        strength: tool.schema.number().describe("Local backend only. How much to redraw, from 0.1 (barely any change) to 0.9 (mostly new). Defaults to 0.6."),
        steps: tool.schema.number().describe("Local backend only. Sampling steps from 1 to 4. Defaults to 2."),
        filename: tool.schema.string().describe("Optional short words describing the edit, used to build the saved filename.")
    },
    async execute(args) {
        const settings = await loadSettings()
        const path = String(args.path || "").trim()
        const instruction = String(args.instruction || "").trim()
        if (!path.startsWith("/home/") || path.includes("..")) return failure("Give the path of an image under /home.")
        if (instruction.length < 3 || instruction.length > 2000) return failure("Describe the edit in 3 to 2000 characters.")

        let mime = extensions[extname(path).toLowerCase()]
        if (!mime) {
            return failure("That file is not an image type the model can read. Use a PNG, JPEG, or WebP file.")
        }
        let size = 0
        try {
            const info = await stat(path)
            if (!info.isFile()) return failure("That path is not a file.")
            size = info.size
        } catch {
            return failure("That image does not exist.")
        }
        if (size > 32 * 1024 * 1024) return failure("That image is larger than 32 MB.")

        const preferred = String(setting(settings, "ASSISTANT_IMAGE_BACKEND", "image_backend", "gemini")).trim().toLowerCase()
        const requested = String(args.backend || preferred || "gemini").trim().toLowerCase()
        if (!["gemini", "local", "auto"].includes(requested)) return failure("Use backend gemini, local, or auto.")

        const python = localPython(settings)
        let localReady = false
        try {
            await access(python)
            localReady = true
        } catch {}
        const key = await resolveKey()

        let backend = requested
        if (backend === "auto") backend = key ? "gemini" : localReady ? "local" : ""
        if (!backend) {
            return failure("Image editing is not available. Add a Gemini API key with bin/assistant-config set-key, or install the local backend with bin/assistant-config setup-image-models.", { configured: false })
        }
        if (backend === "gemini" && !key) {
            return failure("The Gemini backend needs an API key. Run bin/assistant-config set-key, or use the local backend.", { configured: false })
        }
        if (backend === "local" && !localReady) {
            return failure("The local image backend is not installed. Run bin/assistant-config setup-image-models first.", { configured: false })
        }

        const request = { path, instruction, backend }
        if (backend === "gemini" && flag(settings, "ASSISTANT_CONFIRM_IMAGES", "confirm_images")) {
            if (!(await consumeApproval(request))) {
                return failure(
                    "The user has not approved this edit yet. The desktop app will ask them to confirm, then ask you to call edit_image again with exactly these arguments.",
                    { needs_confirmation: true, request }
                )
            }
        }

        const limited = await recordAttempt(settings)
        if (limited) return failure(limited)

        const requestedSteps = Number(args.steps)
        const steps = Number.isFinite(requestedSteps) && requestedSteps > 0 ? Math.max(1, Math.min(Math.round(requestedSteps), 4)) : 2
        const requestedStrength = Number(args.strength)
        const strength = Number.isFinite(requestedStrength) && requestedStrength > 0 ? Math.min(Math.max(requestedStrength, 0.1), 0.9) : 0.6
        const dir = await outputDir(settings)
        const name = `${slug(String(args.filename || "") || instruction)}-${stamp()}.png`
        const destination = join(dir, name)

        if (backend === "local") {
            const timeoutMs = Number(setting(settings, "ASSISTANT_IMAGE_LOCAL_TIMEOUT_MS", "image_local_timeout_ms", 3600000)) || 3600000
            const raw = await runLocal(python, { prompt: instruction, path, steps, strength, model: String(setting(settings, "ASSISTANT_IMAGE_LOCAL_MODEL", "image_model_local", "stabilityai/sdxl-turbo")) }, timeoutMs)
            if (raw.error) return failure(raw.error)
            let parsed: any = null
            for (const line of raw.stdout.trim().split(/\r?\n/).reverse()) {
                if (!line.trim().startsWith("{")) continue
                try {
                    parsed = JSON.parse(line)
                    break
                } catch {}
            }
            if (!parsed || parsed.ok !== true) {
                return failure(String(parsed?.error || "Local editing returned no result."))
            }
            try {
                await saveFrom(parsed.path, destination)
            } catch (error) {
                await unlink(parsed.path).catch(() => {})
                return failure(`Could not save the edited image: ${error instanceof Error ? error.message : String(error)}`)
            }
            return JSON.stringify({
                ok: true,
                action: "edit_image",
                backend: "local",
                path: destination,
                source: path,
                width: parsed.width,
                height: parsed.height,
                steps: parsed.steps,
                strength: parsed.strength,
                device: parsed.device,
                generate_seconds: parsed.generate_seconds,
                note: "Edited on this machine; the image never left it."
            })
        }

        const endpoint = String(setting(settings, "ASSISTANT_GEMINI_ENDPOINT", "", process.env.ASSISTANT_GEMINI_ENDPOINT || "https://generativelanguage.googleapis.com/v1beta")).replace(/\/+$/, "")
        const model = String(setting(settings, "ASSISTANT_IMAGE_MODEL", "image_model_gemini", "gemini-3-pro-image"))
        const timeoutMs = Number(setting(settings, "image_timeout_ms", "", 300000)) || 300000
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), timeoutMs)
        let data: any = null
        try {
            const bytes = await readFile(path)
            const response = await fetch(`${endpoint}/models/${model}:generateContent`, {
                method: "POST",
                headers: { "content-type": "application/json", "x-goog-api-key": key },
                body: JSON.stringify({
                    contents: [
                        {
                            role: "user",
                            parts: [
                                { text: instruction },
                                { inline_data: { mime_type: mime, data: bytes.toString("base64") } }
                            ]
                        }
                    ],
                    generationConfig: { responseModalities: ["TEXT", "IMAGE"] }
                }),
                signal: controller.signal
            })
            const raw = await response.text()
            try {
                data = JSON.parse(raw)
            } catch {
                data = null
            }
            if (!response.ok) {
                const message = String(data?.error?.message || raw.slice(0, 300) || `HTTP ${response.status}`)
                return failure(`Gemini rejected the edit: ${message}`)
            }
        } catch (error) {
            const reason = error instanceof Error && error.name === "AbortError" ? "timed out" : error instanceof Error ? error.message : String(error)
            return failure(`Gemini edit failed: ${reason}`)
        } finally {
            clearTimeout(timer)
        }

        const parts = Array.isArray(data?.candidates?.[0]?.content?.parts) ? data.candidates[0].content.parts : []
        let caption = ""
        let inline: any = null
        for (const part of parts) {
            if (!caption && typeof part?.text === "string" && part.text.trim()) caption = part.text.trim()
            if (!inline) inline = part?.inlineData || part?.inline_data || null
        }
        if (!inline || typeof inline.data !== "string") {
            const blockReason = String(data?.promptFeedback?.blockReason || "")
            if (blockReason) return failure(`Gemini blocked this edit (${blockReason}).`)
            return failure("Gemini returned no edited image.")
        }
        const resultMime = String(inline.mimeType || inline.mime_type || "image/png")
        if (!resultMime.startsWith("image/")) return failure("Gemini returned an unsupported image type.")

        try {
            await mkdir(dir, { recursive: true, mode: 0o755 })
            await writeFile(destination, Buffer.from(inline.data, "base64"), { mode: 0o644 })
        } catch (error) {
            return failure(`Could not save the edited image: ${error instanceof Error ? error.message : String(error)}`)
        }
        return JSON.stringify({
            ok: true,
            action: "edit_image",
            backend: "gemini",
            path: destination,
            source: path,
            mime: resultMime,
            model,
            caption: caption.slice(0, 600)
        })
    }
})
