import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { promisify } from "node:util"
import { join } from "node:path"

const execFileAsync = promisify(execFile)

export default tool({
    description: "Read or update the user's local assistant memory. Use remember only for durable preferences, instructions, project context, personal facts, or explicit requests to remember. Use list when the user asks what is remembered. Never store passwords, tokens, payment details, or other secrets; the user manages deletion in the Memory panel.",
    args: {
        action: tool.schema.string().describe("Memory action: list or remember. Deletion is available only in the assistant Memory panel."),
        text: tool.schema.string().describe("The durable fact or preference to remember."),
        category: tool.schema.string().describe("Memory category: preference, instruction, conversation, project, personal, or other."),
        id: tool.schema.string().describe("Memory entry id for forget."),
        source: tool.schema.string().describe("Short source label for the entry."),
        limit: tool.schema.number().describe("Maximum entries to return for list.")
    },
    async execute(args) {
        const action = String(args.action || "list").trim().toLowerCase()
        const helper = join(process.env.ASSISTANT_APP_DIR || process.cwd(), "assistant_memory.py")
        const command = ["/usr/bin/python3", helper, action]
        if (action === "remember") {
            command.push("--text", String(args.text || "").slice(0, 2000), "--category", String(args.category || "other").slice(0, 32), "--source", String(args.source || "assistant").slice(0, 64))
        } else if (action === "forget" || action === "clear") {
            return JSON.stringify({ ok: false, error: "The user manages memory deletion from the assistant Memory panel." })
        } else if (action === "list") {
            command.push("--limit", String(Math.max(1, Math.min(Number(args.limit) || 32, 500))))
        } else {
            return JSON.stringify({ ok: false, error: "Use list or remember; deletion is available in the Memory panel." })
        }
        try {
            const result = await execFileAsync(command[0], command.slice(1), { cwd: process.cwd(), maxBuffer: 256 * 1024 })
            return String(result.stdout || "").trim() || JSON.stringify({ ok: false, error: "Memory helper returned no result." })
        } catch (error) {
            return JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) })
        }
    }
})
