// Per-browser conveniences only (prompt remembered per run). Storage may be unavailable.

const KEY = 'hackathon-app:prompts'

function load(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, string>
  } catch {
    return {}
  }
}

export function rememberPrompt(runId: string, prompt: string): void {
  try {
    const all = load()
    all[runId] = prompt
    const keys = Object.keys(all)
    for (const k of keys.slice(0, Math.max(0, keys.length - 50))) delete all[k]
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch {
    /* storage unavailable */
  }
}

export function promptFor(runId: string): string {
  return load()[runId] ?? ''
}
