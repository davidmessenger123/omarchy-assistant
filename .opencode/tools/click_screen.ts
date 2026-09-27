import { tool } from "@opencode-ai/plugin"

export default tool({
    description: "Propose a mouse click at a normalized position on the attached screenshot. This tool does not click anything; the desktop app executes low-risk actions automatically and asks the user to confirm sensitive or irreversible actions. Use only when the user asks to click, press, or tap something visible on screen.",
    args: {
        x: tool.schema.number().describe("Horizontal position as a fraction from 0 at the left to 1 at the right of the attached screenshot."),
        y: tool.schema.number().describe("Vertical position as a fraction from 0 at the top to 1 at the bottom of the attached screenshot."),
        target: tool.schema.string().describe("Short description of the visible target, such as Download button or folder icon."),
        button: tool.schema.string().describe("Mouse button: left or right. Defaults to left.")
    },
    async execute(args) {
        const x = Number(args.x)
        const y = Number(args.y)
        const target = String(args.target || "").trim().slice(0, 200)
        const button = String(args.button || "left").trim().toLowerCase()
        if (!Number.isFinite(x) || x < 0 || x > 1 || !Number.isFinite(y) || y < 0 || y > 1 || !target || !["left", "right"].includes(button)) {
            return JSON.stringify({ ok: false, error: "Use x and y between 0 and 1, a target description, and left or right as the button." })
        }
        return JSON.stringify({ ok: true, action: "click", x, y, target, button })
    }
})
