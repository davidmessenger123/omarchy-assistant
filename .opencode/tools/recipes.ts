import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { join } from "node:path"
import { promisify } from "node:util"

const run = promisify(execFile)
const appDir = process.env.ASSISTANT_APP_DIR || process.cwd()
const helper = join(appDir, "assistant_recipes.py")

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
    try {
        const done = await run("/usr/bin/python3", [helper, ...args], { timeout: 20000, maxBuffer: 1024 * 1024 })
        const parsed = JSON.parse(String(done.stdout || "").trim())
        if (!parsed || parsed.ok !== true) {
            return { ok: false as const, error: String(parsed?.error || "the recipe tool could not answer"), problems: parsed?.problems || [], data: parsed }
        }
        return { ok: true as const, data: parsed }
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        if (/ENOENT/.test(message)) {
            return { ok: false as const, error: "the recipe helper is missing from this installation", problems: [], data: null }
        }
        const said = helperError(error)
        if (said) return { ok: false as const, error: String(said.error || "the helper reported a problem"), problems: said.problems || [], data: said }
        return { ok: false as const, error: `the recipe tool could not answer: ${message.slice(0, 160)}`, problems: [], data: null }
    }
}

export default tool({
    description: "Manage recipes: named multi-step procedures the user describes once and reuses later, such as installing a game or messaging someone in Teams. Use save when the user asks you to remember how to do something, list and show to answer what recipes exist, and dry_run to show exactly what a recipe would do before it ever runs. Saving is only ever proposed: the user sees every step and every command spelled out and has to approve it. Never tell the user a recipe was saved before the desktop app confirms it, and never invent that a recipe exists.",
    args: {
        action: tool.schema.string().describe("One of: list, show, dry_run, save, delete."),
        name: tool.schema.string().describe("Recipe name, lowercase with dashes, such as install-steam-game."),
        recipe: tool.schema.string().describe("The full recipe as a JSON object, for action save."),
        values: tool.schema.string().describe("Values for the recipe's placeholders as name=value pairs separated by commas, for dry_run, such as person=Carl Elphick,message=Hello.")
    },
    async execute(args) {
        const action = String(args.action || "list").trim().toLowerCase()
        const allowed = ["list", "show", "dry_run", "run", "save", "delete"]
        if (!allowed.includes(action)) return JSON.stringify({ ok: false, error: `Choose one of: ${allowed.join(", ")}.` })
        const name = String(args.name || "").trim().slice(0, 60)

        if (action === "list") {
            const listed = await helperCall(["list"])
            if (!listed.ok) return JSON.stringify({ ok: false, action: "recipes", error: listed.error })
            const data = listed.data as { recipes?: unknown[]; count?: number }
            return JSON.stringify({ ok: true, action: "recipes", op: action, count: Number(data.count) || 0, recipes: data.recipes || [] })
        }

        if (action === "show" || action === "dry_run") {
            if (!name) return JSON.stringify({ ok: false, error: "Say which recipe by name." })
            const vars = String(args.values || "")
                .split(",")
                .map((pair) => pair.trim())
                .filter((pair) => pair.includes("="))
            const command = action === "show" ? ["show", name] : ["dry-run", name, ...vars.flatMap((pair) => ["--var", pair])]
            const shown = await helperCall(command)
            if (!shown.ok) {
                return JSON.stringify({ ok: false, action: "recipes", op: action, error: shown.error, missing: shown.data?.missing || [] })
            }
            const data = shown.data as { lines?: string[]; recipe?: Record<string, unknown>; runs_commands?: boolean }
            return JSON.stringify({
                ok: true,
                action: "recipes",
                op: action,
                recipe: data.recipe || {},
                lines: data.lines || [],
                runs_commands: data.runs_commands === true
            })
        }

        if (action === "run") {
            if (!name) return JSON.stringify({ ok: false, error: "Say which recipe to run by name." })
            const pairs = String(args.values || "")
                .split(",")
                .map((pair) => pair.trim())
                .filter((pair) => pair.includes("="))
            // The same check the desktop app makes, so the user is never asked to
            // approve a run that is going to be refused.
            const preview = await helperCall(["run", name, ...pairs.flatMap((pair) => ["--var", pair])])
            if (!preview.ok) {
                return JSON.stringify({
                    ok: false,
                    action: "recipes",
                    op: action,
                    error: preview.error,
                    detail: preview.data?.detail,
                    unsupported: preview.data?.unsupported,
                    missing: preview.data?.missing || [],
                    hint: preview.data?.missing?.length
                        ? "Ask the user for the missing values, then run it again."
                        : preview.data?.unsupported
                            ? "Tell the user which step kind is blocking it. Do not offer to work around it by improvising the steps yourself."
                            : undefined
                })
            }
            const data = preview.data as { lines?: string[]; recipe?: Record<string, unknown> }
            const lines = data.lines || []
            return JSON.stringify({
                ok: true,
                action: "recipe",
                operation: "run",
                recipe_name: name,
                values: String(args.values || ""),
                title: String(data.recipe?.title || name),
                summary: "Run the recipe \"" + String(data.recipe?.title || name) + "\"",
                lines,
                note: "Every step that touches the screen is approved separately as the recipe runs, and a step that says STOP AND ASK waits for the user."
            })
        }

        if (action === "delete") {
            if (!name) return JSON.stringify({ ok: false, error: "Say which recipe to delete by name." })
            return JSON.stringify({
                ok: true,
                action: "recipe",
                operation: "delete",
                recipe_name: name,
                summary: `Delete the recipe "${name}"`,
                lines: ["This permanently removes the recipe. It cannot be undone from here."]
            })
        }

        // save: validate first, then propose with the steps spelled out in full.
        const raw = String(args.recipe || "").trim()
        if (!raw) return JSON.stringify({ ok: false, error: "A saved recipe needs the recipe itself. Build the JSON object with name, title and steps." })
        const checked = await helperCall(["validate", raw])
        if (!checked.ok) {
            return JSON.stringify({
                ok: false,
                action: "recipes",
                op: action,
                error: checked.error,
                problems: checked.problems,
                hint: "Fix these and try again. Do not save a recipe that does not validate."
            })
        }
        const lines = (checked.data as { lines?: string[] }).lines || []
        const draft = JSON.parse(raw) as Record<string, unknown>
        const steps = Array.isArray(draft.steps) ? draft.steps : []
        const commands = steps.filter((step) => step && typeof step === "object" && Array.isArray((step as Record<string, unknown>).command)).length
        const gates = steps.filter((step) => step && typeof step === "object" && typeof (step as Record<string, unknown>).ask === "string").length
        return JSON.stringify({
            ok: true,
            action: "recipe",
            operation: "save",
            recipe: raw,
            recipe_name: String(draft.name || ""),
            summary: `Save the recipe "${String(draft.title || draft.name || "")}"`,
            lines,
            command_count: commands,
            gate_count: gates,
            note: commands > 0
                ? `This recipe runs ${commands} command${commands === 1 ? "" : "s"} on this machine. The user is approving those commands, so spell them out when you describe it.`
                : "This recipe only opens apps and clicks, it runs no commands."
        })
    }
})
