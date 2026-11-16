// Checks the whole bridge without Claude: starts the MCP server the way Claude Code would, asks PeakCut for its
// "Save and test" request, takes it through next_request, answers through submit_answer, and checks PeakCut got it.
//
//   node selftest.mjs [PeakCut address] [token file]
// PeakCut must be running with the Claude Code session chosen as its AI (Settings, AI, or PEAKCUT_AI_PROVIDER).
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const base = process.argv[2] || 'http://127.0.0.1:8765'
const env = { ...process.env, PEAKCUT_URL: base, ...(process.argv[3] ? { PEAKCUT_BRAIN_TOKEN_FILE: process.argv[3] } : {}) }
const client = new Client({ name: 'selftest', version: '1.0.0' })
await client.connect(new StdioClientTransport({ command: process.execPath, args: ['server.ts'], cwd: import.meta.dirname, env, stderr: 'ignore' }))
const fails = []
const check = (name, ok, extra = '') => { console.log((ok ? 'PASS ' : 'FAIL ') + name + (ok ? '' : '  ' + extra)); if (!ok) fails.push(name) }

const tools = (await client.listTools()).tools.map(t => t.name).sort()
check('the server offers next_request and submit_answer', tools.join() === 'next_request,submit_answer', tools.join())
await new Promise(r => setTimeout(r, 1500))                 // let it open /api/brain/events, so PeakCut sees a brain

const testing = fetch(base + '/api/ai/test', { method: 'POST' }).then(r => r.json().then(b => [r.status, b]))
const got = JSON.parse((await client.callTool({ name: 'next_request', arguments: { wait_seconds: 20 } })).content[0].text)
check('a waiting request is handed over', got.id && got.purpose === 'Settings: test' && got.prompt.includes('"items"'), JSON.stringify(got).slice(0, 200))

const bad = await client.callTool({ name: 'submit_answer', arguments: { id: got.id, answer: 'ok!' } })
check('a broken answer comes back as an error the brain can fix', bad.isError && bad.content[0].text.includes('Not valid JSON'), JSON.stringify(bad))
const good = await client.callTool({ name: 'submit_answer', arguments: { id: got.id, answer: '{"items": ["ok"]}' } })
check('the corrected answer is accepted', !good.isError, JSON.stringify(good))

const [status, body] = await testing
check('PeakCut\'s "Save and test" succeeds with it', status === 200 && body.ok === true, JSON.stringify([status, body]))
const idle = JSON.parse((await client.callTool({ name: 'next_request', arguments: { wait_seconds: 1 } })).content[0].text)
check('then the queue is idle', idle.idle === true, JSON.stringify(idle))

await client.close()
console.log(fails.length ? '\nFAILED: ' + fails.join(', ') : '\nALL PASSED')
process.exit(fails.length ? 1 : 0)
