import { tool } from "@opencode-ai/plugin"

const MAX_CHARS = 20000
const WTYPE_SAFE = 4000

export default tool({
    description: "Type a long or awkward piece of text into the field the user currently has focused. Prefer this over screen_type whenever the text is longer than a few words, contains newlines, lists, code, quotes, or non-English characters, because synthetic keystrokes mangle those. It deliberately does not use the clipboard, so whatever the user had copied is left alone. The text is never stored in the action history, only its length. This still proposes the action and the user approves it first.",
    args: {
        text: tool.schema.string().describe("The exact text to type, including newlines. Up to 20000 characters."),
        target: tool.schema.string().describe("Short description of the field receiving the text, shown in the approval card."),
        enter: tool.schema.boolean().describe("Whether to press Enter after typing. Leave false unless the user asked for a new line or to submit.")
    },
    async execute(args) {
        const raw = String(args.text ?? "")
        if (!raw.trim()) {
            return JSON.stringify({ ok: false, error: "There is no text to type." })
        }
        if (raw.includes("\u0000")) {
            return JSON.stringify({ ok: false, error: "That text contains a null character and cannot be typed." })
        }
        if (raw.length > MAX_CHARS) {
            return JSON.stringify({
                ok: false,
                error: `That is ${raw.length} characters, more than the ${MAX_CHARS} limit. Ask the user to shorten it or split it into parts.`
            })
        }
        const target = String(args.target || "").trim().slice(0, 200)
        return JSON.stringify({
            ok: true,
            action: "type",
            text: raw,
            target: target || "the focused field",
            enter: args.enter === true,
            source: "type_text",
            // Long text is the case worth telling the user about.
            note: raw.length > WTYPE_SAFE ? `long text, ${raw.length} characters` : ""
        })
    }
})
