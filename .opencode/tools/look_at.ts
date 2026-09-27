import { tool } from "@opencode-ai/plugin"

export default tool({
    description: "Ask the desktop app for a fresh screenshot of the user's screen. Use this when you need to see something you cannot infer: an error message, the current state of an application, what a dialog says, or the result of an action the user described. The app cannot deliver the image inside this turn, so call this tool and then stop; the screenshot arrives in the next message. Do not guess at screen contents instead of looking, and do not call this more than a couple of times for one request.",
    args: {
        reason: tool.schema.string().describe("What you need to see on the screen, such as the error dialog behind the settings window."),
        target: tool.schema.string().describe("Optional hint about what to look at, such as a window title or a visible control.")
    },
    async execute(args) {
        const reason = String(args.reason || "").trim()
        if (reason.length < 3) return JSON.stringify({ ok: false, error: "Say what you need to see on the screen." })
        return JSON.stringify({
            ok: true,
            action: "look_at",
            reason: reason.slice(0, 400),
            target: String(args.target || "").trim().slice(0, 120),
            delivered: false,
            note: "Stop this turn now. The desktop app captures the screen and sends it to you in the next message."
        })
    }
})
