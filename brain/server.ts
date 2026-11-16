// MCP server "peakcut": the Claude Code session's only way to see and answer PeakCut's AI requests.
//
// It holds no state. Each tool call is passed on to PeakCut's server on this computer (/api/brain/*), signed with
// the secret PeakCut writes to data/brain.token when it starts. It also keeps /api/brain/events open, which is how
// PeakCut knows the session is running, and (in channel mode) wakes the session when work arrives.
//
//   PEAKCUT_URL                where PeakCut answers (default http://127.0.0.1:8765)
//   PEAKCUT_BRAIN_TOKEN_FILE   the secret (default ../data/brain.token, next to this folder)
//   PEAKCUT_BRAIN_CHANNEL=1    push a "work waiting" event into the session (needs --dangerously-load-development-channels)
//
// Runs with Node 22.18+ directly (`node server.ts`): only type annotations, which Node strips by itself.
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const BASE = (process.env.PEAKCUT_URL || 'http://127.0.0.1:8765').replace(/\/$/, '')
const TOKEN_FILE = process.env.PEAKCUT_BRAIN_TOKEN_FILE || resolve(HERE, '..', 'data', 'brain.token')
const CHANNEL = process.env.PEAKCUT_BRAIN_CHANNEL === '1'
const MAX_WAIT = 290            // PeakCut caps a single wait at 300 s (PEAKCUT_BRAIN_MAX_WAIT)

function token(): string {
  try {
    return readFileSync(TOKEN_FILE, 'utf8').trim()       // read every time: PeakCut makes a new one on each start
  } catch {
    return ''
  }
}

async function peakcut(path: string, init: RequestInit = {}, timeoutMs = 30_000): Promise<any> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { 'X-PeakCut-Brain': token(), 'Content-Type': 'application/json', ...(init.headers || {}) },
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (res.status === 401) throw new Error('PeakCut refused the brain secret. Restart the brain after PeakCut has started.')
  if (!res.ok) throw new Error(`PeakCut answered ${res.status}.`)
  return res.status === 204 ? { idle: true } : res.json()
}

const INSTRUCTIONS = [
  'This server connects you to PeakCut, a clipping app on this computer that uses you as its judging model.',
  'Call next_request. If it returns a request, do exactly what its prompt says and send the result only through',
  'submit_answer, as JSON in the shape the prompt asks for. Then call next_request again; keep going until it',
  'returns idle, then end your turn with one short line.',
  CHANNEL ? 'A <channel source="peakcut"> event means requests are waiting: drain the queue the same way.' : '',
].filter(Boolean).join(' ')

const mcp = new Server(
  { name: 'peakcut', version: '1.0.0' },
  {
    capabilities: { tools: {}, ...(CHANNEL ? { experimental: { 'claude/channel': {} } } : {}) },
    instructions: INSTRUCTIONS,
  },
)

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'next_request',
      description: 'Take the next waiting PeakCut request. Returns {id, purpose, prompt}, or {idle: true} when nothing ' +
        'arrived within wait_seconds. Waiting is free: prefer a long wait over calling again quickly.',
      inputSchema: {
        type: 'object',
        properties: { wait_seconds: { type: 'number', description: `How long to wait for work, 0-${MAX_WAIT}. Default 240.` } },
      },
    },
    {
      name: 'submit_answer',
      description: 'Answer one request. `answer` is the JSON text only, no code fences or commentary. If PeakCut says ' +
        'it is not valid JSON, fix it and submit once more for the same id.',
      inputSchema: {
        type: 'object',
        properties: { id: { type: 'string' }, answer: { type: 'string', description: 'The JSON answer, as text.' } },
        required: ['id', 'answer'],
      },
    },
  ],
}))

let lastAsked = 0
let asking = 0            // next_request calls waiting right now: the session is already listening
const text = (v: unknown, isError = false) => ({ content: [{ type: 'text', text: typeof v === 'string' ? v : JSON.stringify(v) }], isError })

mcp.setRequestHandler(CallToolRequestSchema, async req => {
  const args = (req.params.arguments || {}) as Record<string, unknown>
  try {
    if (req.params.name === 'next_request') {
      const wait = Math.max(0, Math.min(MAX_WAIT, Number(args.wait_seconds ?? 240) || 0))
      asking++
      try {
        return text(await peakcut(`/api/brain/next?wait=${wait}`, {}, (wait + 30) * 1000))
      } finally {
        asking--
        lastAsked = Date.now()
      }
    }
    if (req.params.name === 'submit_answer') {
      const got = await peakcut('/api/brain/answer', { method: 'POST', body: JSON.stringify({ id: String(args.id || ''), answer: String(args.answer ?? '') }) })
      return text(got.message || got, !got.ok)
    }
    return text(`Unknown tool ${req.params.name}`, true)
  } catch (e) {
    const why = e instanceof Error ? e.message : String(e)
    return text(`PeakCut is not reachable at ${BASE} (${why}). End your turn; try again on the next loop.`, true)
  }
})

await mcp.connect(new StdioServerTransport())

// ---- keep /api/brain/events open: PeakCut's "brain connected" light, and the channel wake-up
let pendingNow = 0
let nudgedAt = 0

async function nudge(n: number) {
  if (!CHANNEL) return
  nudgedAt = Date.now()
  await mcp.notification({
    method: 'notifications/claude/channel',
    params: { content: `${n} PeakCut request${n === 1 ? '' : 's'} waiting. Drain the queue with next_request and submit_answer.`, meta: { pending: String(n) } },
  }).catch(() => {})
}

async function follow() {
  for (let backoff = 1000; ; backoff = Math.min(backoff * 2, 30_000)) {
    try {
      const res = await fetch(BASE + '/api/brain/events', { headers: { 'X-PeakCut-Brain': token() } })
      if (!res.ok || !res.body) throw new Error(String(res.status))
      backoff = 1000
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let i: number
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).replace(/^data: /, '')
          buf = buf.slice(i + 2)
          let n = 0
          try { n = Number(JSON.parse(line).pending) || 0 } catch { continue }
          const was = pendingNow
          pendingNow = n
          if (n > 0 && was === 0) await nudge(n)
        }
      }
    } catch {
      // PeakCut is not running yet, or restarted: try again shortly
    }
    await new Promise(r => setTimeout(r, backoff))
  }
}

// Events can be dropped silently, so while work keeps waiting and nobody has asked for it, nudge again each minute.
setInterval(() => {
  const now = Date.now()
  if (pendingNow > 0 && asking === 0 && now - lastAsked > 60_000 && now - nudgedAt > 60_000) void nudge(pendingNow)
}, 15_000)

void follow()
