import { tool } from "@opencode-ai/plugin"
import { mkdir, readFile, writeFile } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, join } from "node:path"

const model = process.env.ASSISTANT_IMAGE_MODEL || "gemini-3-pro-image"
const endpoint = (process.env.ASSISTANT_GEMINI_ENDPOINT || "https://generativelanguage.googleapis.com/v1beta").replace(/\/+$/, "")
const aspectRatios = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
const resolutions = ["1K", "2K", "4K"]
const maxBytes = 64 * 1024 * 1024
const extensions: Record<string, string> = { "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp" }

function firstLine(value: unknown) {
    return String(value ?? "").split(/\r?\n/)[0].trim()
}

function expandHome(value: string) {
    if (value === "$HOME") return homedir()
    if (value.startsWith("$HOME/")) return join(homedir(), value.slice(6))
    return value
}

function slug(value: string) {
    const cleaned = value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48)
    return cleaned || "image"
}

function stamp() {
    const now = new Date()
    const pad = (value: number) => String(value).padStart(2, "0")
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

function limit(name: string, fallback: number) {
    const value = Number(process.env[name])
    return Number.isFinite(value) && value >= 0 ? value : fallback
}

async function resolveKey() {
    const file = join(homedir(), ".config", "omarchy-assistant", "gemini.key")
    const fromEnv = firstLine(process.env.GEMINI_API_KEY)
    if (fromEnv.length >= 20) return { key: fromEnv, source: "GEMINI_API_KEY", file }
    try {
        const stored = firstLine(await readFile(file, "utf8"))
        if (stored.length >= 20) return { key: stored, source: file, file }
    } catch {}
    return { key: "", source: file, file }
}

async function outputDir() {
    const override = firstLine(process.env.ASSISTANT_IMAGE_DIR)
    if (override) return expandHome(override)
    const configured = firstLine(process.env.XDG_PICTURES_DIR)
    if (configured) return join(expandHome(configured), "omarchy-assistant")
    try {
        const text = await readFile(join(homedir(), ".config", "user-dirs.dirs"), "utf8")
        const match = text.match(/XDG_PICTURES_DIR\s*=\s*"?([^"\n]+)"?/)
        if (match) return join(expandHome(match[1].trim()), "omarchy-assistant")
    } catch {}
    return join(homedir(), "Pictures", "omarchy-assistant")
}

async function recordAttempt() {
    const hourly = limit("ASSISTANT_IMAGE_HOURLY_LIMIT", 10)
    const daily = limit("ASSISTANT_IMAGE_DAILY_LIMIT", 60)
    const file = join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "omarchy-assistant", "image-usage.json")
    const now = Date.now()
    let stamps: number[] = []
    try {
        const parsed = JSON.parse(await readFile(file, "utf8"))
        if (Array.isArray(parsed?.stamps)) stamps = parsed.stamps.map(Number).filter((value: unknown) => Number.isFinite(value))
    } catch {}
    stamps = stamps.filter((value) => now - value < 24 * 60 * 60 * 1000)
    if (stamps.filter((value) => now - value < 60 * 60 * 1000).length >= hourly) {
        return `Hourly image generation limit reached (${hourly} images). Wait for the window to clear or raise ASSISTANT_IMAGE_HOURLY_LIMIT.`
    }
    if (stamps.length >= daily) {
        return `Daily image generation limit reached (${daily} images). Wait until tomorrow or raise ASSISTANT_IMAGE_DAILY_LIMIT.`
    }
    stamps.push(now)
    try {
        await mkdir(dirname(file), { recursive: true, mode: 0o700 })
        await writeFile(file, `${JSON.stringify({ stamps })}\n`, { mode: 0o600 })
    } catch {}
    return ""
}

function failure(error: string, extra: Record<string, unknown> = {}) {
    return JSON.stringify({ ok: false, action: "generate_image", error, ...extra })
}

export default tool({
    description: "Generate an image from a text prompt with Google's Gemini image model and save it under the user's Pictures directory. Use this when the user asks to create, draw, generate, design, or make a picture, logo, illustration, poster, or photo. The tool returns the saved absolute path. It cannot edit an existing image and it never overwrites files.",
    args: {
        prompt: tool.schema.string().describe("Detailed description of the image to generate, including subject, style, composition, lighting, and any text that should appear."),
        aspect_ratio: tool.schema.string().describe("Aspect ratio: 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, or 21:9. Defaults to 1:1."),
        resolution: tool.schema.string().describe("Output resolution: 1K, 2K, or 4K. Defaults to 1K. Use 2K or 4K only when the user asks for a high-resolution or large image."),
        filename: tool.schema.string().describe("Optional short words describing the image, used to build the saved filename.")
    },
    async execute(args) {
        const prompt = String(args.prompt || "").trim()
        if (prompt.length < 3 || prompt.length > 4000) return failure("Give an image prompt between 3 and 4000 characters.")
        const aspectRatio = String(args.aspect_ratio || "1:1").trim()
        if (!aspectRatios.includes(aspectRatio)) return failure(`Use one of these aspect ratios: ${aspectRatios.join(", ")}.`)
        const resolution = String(args.resolution || "1K").trim().toUpperCase()
        if (!resolutions.includes(resolution)) return failure(`Use one of these resolutions: ${resolutions.join(", ")}.`)

        const credential = await resolveKey()
        if (!credential.key) {
            return failure("Image generation is not configured. Ask the user to run bin/assistant-config set-key in the assistant directory, or to export GEMINI_API_KEY, then try again.", { configured: false })
        }
        const limited = await recordAttempt()
        if (limited) return failure(limited)

        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), limit("ASSISTANT_IMAGE_TIMEOUT_MS", 300000))
        let data: any = null
        try {
            const response = await fetch(`${endpoint}/models/${model}:generateContent`, {
                method: "POST",
                headers: { "content-type": "application/json", "x-goog-api-key": credential.key },
                body: JSON.stringify({
                    contents: [{ role: "user", parts: [{ text: prompt }] }],
                    generationConfig: {
                        responseModalities: ["TEXT", "IMAGE"],
                        imageConfig: { aspectRatio, imageSize: resolution }
                    }
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
                const message = firstLine(data?.error?.message) || raw.slice(0, 300) || `HTTP ${response.status}`
                return failure(`Gemini rejected the image request: ${message}`)
            }
        } catch (error) {
            const reason = error instanceof Error && error.name === "AbortError" ? "timed out" : error instanceof Error ? error.message : String(error)
            return failure(`Gemini image request failed: ${reason}`)
        } finally {
            clearTimeout(timer)
        }

        const candidates = Array.isArray(data?.candidates) ? data.candidates : []
        const parts = Array.isArray(candidates[0]?.content?.parts) ? candidates[0].content.parts : []
        let caption = ""
        let inline: any = null
        for (const part of parts) {
            if (!caption && typeof part?.text === "string" && part.text.trim()) caption = part.text.trim()
            if (!inline) inline = part?.inlineData || part?.inline_data || null
        }
        if (!inline || typeof inline.data !== "string" || !inline.data) {
            const blockReason = firstLine(data?.promptFeedback?.blockReason)
            if (blockReason) return failure(`Gemini blocked this image request (${blockReason}). Try rephrasing the prompt.`)
            const finishReason = firstLine(candidates[0]?.finishReason)
            if (finishReason && finishReason !== "STOP") return failure(`Gemini returned no image (${finishReason}). Try a different prompt.`)
            return failure("Gemini returned no image data. Try a more descriptive prompt.")
        }
        const mime = firstLine(inline.mimeType || inline.mime_type)
        const extension = extensions[mime.toLowerCase()]
        if (!extension) return failure(`Gemini returned an unsupported image type (${mime || "unknown"}).`)
        if (inline.data.length > Math.ceil(maxBytes * 1.4)) return failure("The generated image is larger than the 64 MB limit.")

        const dir = await outputDir()
        const name = `${slug(firstLine(args.filename) || prompt)}-${stamp()}${extension}`
        const path = join(dir, name)
        try {
            await mkdir(dir, { recursive: true, mode: 0o755 })
            await writeFile(path, Buffer.from(inline.data, "base64"), { mode: 0o644 })
        } catch (error) {
            return failure(`Could not save the generated image: ${error instanceof Error ? error.message : String(error)}`)
        }
        return JSON.stringify({
            ok: true,
            action: "generate_image",
            path,
            mime,
            bytes: Buffer.from(inline.data, "base64").length,
            model,
            aspect_ratio: aspectRatio,
            resolution,
            caption: caption.slice(0, 800)
        })
    }
})
