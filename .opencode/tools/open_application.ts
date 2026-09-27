import { tool } from "@opencode-ai/plugin"
import { readdir, readFile } from "node:fs/promises"
import { basename, join } from "node:path"

function normalize(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim()
}

function parseDesktopEntry(text, id, source) {
    let section = ""
    let name = ""
    let genericName = ""
    let keywords = ""
    let exec = ""
    let type = ""
    let noDisplay = false
    let hidden = false
    for (const line of String(text || "").split(/\r?\n/)) {
        if (line.startsWith("[") && line.endsWith("]")) {
            section = line.slice(1, -1)
            continue
        }
        if (section !== "Desktop Entry") continue
        const separator = line.indexOf("=")
        if (separator < 0) continue
        const key = line.slice(0, separator).trim()
        const value = line.slice(separator + 1).trim()
        if (key === "Name") name ||= value
        if (key === "GenericName") genericName ||= value
        if (key === "Keywords") keywords ||= value
        if (key === "Exec") exec ||= value
        if (key === "Type") type ||= value
        if (key === "NoDisplay") noDisplay ||= value.toLowerCase() === "true"
        if (key === "Hidden") hidden ||= value.toLowerCase() === "true"
    }
    if (noDisplay || hidden || (type && type !== "Application")) return null
    const displayName = name || id
    return {
        id,
        name: displayName,
        search: normalize([displayName, genericName, keywords].filter(Boolean).join(" ")),
        exec,
        source
    }
}

async function installedApplications() {
    const roots = [
        process.env.XDG_DATA_HOME ? join(process.env.XDG_DATA_HOME, "applications") : "",
        process.env.HOME ? join(process.env.HOME, ".local", "share", "applications") : "",
        "/usr/local/share/applications",
        "/usr/share/applications"
    ].filter((value, index, values) => value && values.indexOf(value) === index)
    const applications = new Map()
    for (const root of roots) {
        let files
        try {
            files = await readdir(root, { withFileTypes: true })
        } catch {
            continue
        }
        for (const file of files) {
            if (!file.isFile() || !file.name.endsWith(".desktop")) continue
            const id = basename(file.name, ".desktop")
            if (applications.has(id)) continue
            try {
                const text = await readFile(join(root, file.name), "utf8")
                const application = parseDesktopEntry(text, id, join(root, file.name))
                if (application) applications.set(id, application)
            } catch {
                continue
            }
        }
    }
    return [...applications.values()].sort((left, right) => left.name.localeCompare(right.name))
}

export default tool({
    description: "Propose launching an installed graphical application by visible name or desktop ID. This tool does not launch anything; the desktop app executes the proposal and asks for confirmation for sensitive applications.",
    args: {
        application: tool.schema.string().describe("Installed application name or desktop ID, such as Firefox, Foot, or org.gnome.Nautilus.")
    },
    async execute(args) {
        const query = String(args.application || "").trim()
        if (!query || query.length > 128 || /[\u0000-\u001f]/.test(query)) {
            return "I need an installed application name."
        }
        const normalizedQuery = normalize(query.replace(/\.desktop$/i, ""))
        const applications = await installedApplications()
        const exact = applications.filter(application => normalize(application.name) === normalizedQuery || normalize(application.id) === normalizedQuery)
        const matches = exact.length > 0
            ? exact
            : applications.filter(application => application.search.includes(normalizedQuery))
        if (matches.length === 0) {
            return `I could not find an installed application named ${query}.`
        }
        if (matches.length > 1) {
            const suggestions = matches.slice(0, 8).map(application => `${application.name} (${application.id})`).join(", ")
            return `More than one application matches ${query}: ${suggestions}. Please be more specific.`
        }
        const application = matches[0]
        return JSON.stringify({ ok: true, action: "open_application", application: application.name, id: application.id, exec: application.exec, target: application.name, source: application.source })
    }
})
