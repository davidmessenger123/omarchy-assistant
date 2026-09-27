import { tool } from "@opencode-ai/plugin"

export default tool({
    description: "Propose typing text into the currently focused desktop field. This does not type anything; the desktop app executes low-risk actions automatically and asks for confirmation for sensitive or irreversible actions.",
    args: {
        text: tool.schema.string().describe("Text to type into the focused field, up to 4000 characters."),
        target: tool.schema.string().describe("Short description of the field or control receiving the text."),
        enter: tool.schema.boolean().describe("Whether to press Enter after typing.")
    },
    async execute(args) {
        const text = String(args.text || "").slice(0, 4000)
        const target = String(args.target || "").trim().slice(0, 200)
        const enter = args.enter === true
        if (!text || !target || text.includes("\u0000")) {
            return JSON.stringify({ ok: false, error: "Provide non-empty text and a target description." })
        }
        return JSON.stringify({ ok: true, action: "type", text, target, enter })
    }
})
