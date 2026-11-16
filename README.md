# Clipping Studio

Turn long videos and live streams into captioned vertical clips for paid clipping campaigns, on your own laptop.
The AI work (finding moments, hooks, titles, the campaign check) is done by a Claude Code session running next to
the app, on your normal Claude subscription. No API key is needed anywhere.

Paste a YouTube, Twitch or Kick link (or upload a file). Clipping Studio transcribes it on your machine, has the
Claude session pick the moments worth clipping, cuts them to 9:16 with word-by-word captions and a hook, checks each
clip against the campaign brief, and can post it and track it through to payment. Paste a live link and it records
the stream and clips the highlights while it is still running.

Built on [PeakCut](https://github.com/haziqahmedqasmi/PeakCut) (MIT).

## What it does

- **Clips from any video.** Speech is transcribed locally; the Claude session reads the transcript and picks the
  moments that would hold a stranger's attention.
- **Live clipping.** Records streams and clips as they happen. Creators linked to an active campaign are recorded
  automatically when they go live, in the order you set.
- **Campaigns.** Import a Content Rewards campaign from its link, or add one by hand. Each creator can have a
  default campaign; the brief steers which moments are picked.
- **Campaign check.** Every finished clip is checked against the rules (length, disclosure, campaign still
  accepting) and the brief. A clip that fails cannot be posted or submitted unless you press Override.
- **Submission tracker.** Each clip moves through made, approved, posted, submitted, paid or rejected, with
  estimated earnings after the platform fee. The Creator Terms are enforced in code: one campaign per post, no
  resubmitting a rejected post, a warning before the 30-minute submit window closes.
- **Playbooks.** Recipes for how a clip is presented (result first, best line first, camera over gameplay, full
  camera and more), rotated so an account never looks templated. Stored as files in `playbooks/`.
- **Results.** Views grouped by playbook, length, hour and campaign; account health; and a weekly review from the
  Claude session whose suggestions you accept or reject. Nothing changes on its own.
- **Posting.** YouTube, Instagram and TikTok, queued or automatic, with spacing and daily caps (see
  [DEPLOY.md](DEPLOY.md#connecting-youtube-instagram-and-tiktok) for the one-time setup).

## What it never does

- Use an API key or read your Claude login. The brain is an ordinary Claude Code session you are signed in to.
- Give the brain a shell, files or the web. It can only take the next request and send an answer.
- Log in for you. You sign in to Content Rewards yourself, once, in a visible window.
- Join campaigns, request payouts, run more than one account, hide itself, or do anything that inflates views.

## Set up (Windows)

You need:

- Python 3.10 or newer, and `ffmpeg` (`winget install Gyan.FFmpeg`)
- Node.js 22.18 or newer (the brain's MCP server runs as TypeScript directly)
- Claude Code (`npm install -g @anthropic-ai/claude-code`), signed in with your claude.ai account
- Microsoft Edge (already on Windows; used to read Content Rewards pages)

Then, in the project folder:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd brain; npm install; cd ..
```

Make sure `ANTHROPIC_API_KEY` is **not** set, or Claude Code bills that key instead of your subscription.

## Start and stop

| What | How |
|---|---|
| Everything | double-click `start.cmd`: the app runs hidden, the brain opens in its own window, the dashboard opens in the browser |
| App only, no AI | `start.cmd -NoBrain` |
| Stop everything | `stop.cmd` |

The dashboard is at http://127.0.0.1:8765/app. The first start downloads the speech model (about 480 MB, once).

**First start of the brain:** its window asks "Do you trust the files in this folder?" with **No, exit** selected.
Press the down arrow, then Enter. Its tools do not load until you do.

A green dot and "Brain connected" at the bottom left of the dashboard means it is working. [RUNBOOK.md](RUNBOOK.md)
covers what to do when it is not, the measurements so far, and what still needs checking.

## Other AI services

`start.cmd` uses the Claude Code session. You can switch to Gemini, OpenAI, Grok, Groq, OpenRouter, the Claude API,
a local model through Ollama, or any OpenAI-compatible service in **Settings, AI**: pick the service, paste a key,
press **Save and test**. Speech to text never uses the AI service; audio stays on your machine.

## Settings

Everything is set through environment variables, normally in a `.env` file next to this README.
[`.env.example`](.env.example) lists them. The ones specific to this version:

| Setting | What it is for |
|---|---|
| `PEAKCUT_AI_PROVIDER=claude_session` | Use the Claude Code session as the AI (`start.cmd` sets this) |
| `PEAKCUT_BRAIN_BACKLOG` | How many requests may wait before the oldest live checks are skipped (default 4) |
| `PEAKCUT_WATCH_EVERY_MIN` | How often watched creators are checked for going live, in minutes (default 2) |
| `PEAKCUT_CR_FEE` | The platform fee taken from campaign earnings (default 0.10) |
| `PEAKCUT_DATA` | Where videos, clips and the database are kept (default `./data`) |

Settings keep the `PEAKCUT_` prefix from the original project. `.env`, `secrets/`, `data/` and `run/` are ignored by
git. Keep keys there, never in the code.

## How it is built

| Part | What it is |
|---|---|
| `studio/` | The server: FastAPI, the clip pipeline, live recording, campaigns, posting |
| `studio/bridge.py`, `brain/` | The request queue for the Claude session, and the small MCP server it talks through |
| `studio/campaigns.py`, `studio/cr.py` | Campaigns and the check; Content Rewards through Playwright |
| `studio/playbooks.py`, `playbooks/` | Clip recipes and their rotation |
| `studio/insights.py` | The Results page and the weekly review |
| `web/` | The pages, plain HTML, CSS and JavaScript, no build step |
| `scripts/`, `start.cmd`, `stop.cmd` | Starting and stopping the app and the brain on Windows |
| `tests/` | Tests that run against a throwaway copy of the app |

[AGENTS.md](AGENTS.md) is a short map of the code for contributors and AI coding assistants.

## Tests

```powershell
$env:PYTHONUTF8 = "1"; .venv\Scripts\python tests/run.py
```

This starts a copy of the app on a spare port with an empty data folder, runs every test suite against it, then
removes it. It needs no keys, no brain and never touches real data. `node brain/selftest.mjs` checks the MCP server
on its own.

## Security

- The app listens on this computer only (`127.0.0.1`) and refuses changes sent by other websites. Do not expose it
  to the internet; for a shared server, turn accounts on (`PEAKCUT_ACCOUNTS=1`, see [DEPLOY.md](DEPLOY.md)).
- The brain's doors answer only on this computer, only with the token in `data/brain.token`.
- Text from videos and campaign briefs is treated as data, never as instructions, when it is shown to the AI.

## Using it responsibly

Clip only creators whose campaigns you have joined, or videos you have permission to use. You are responsible for
the rights to what you download, clip and post, and for following the terms of every site you fetch from, post to
and submit to.

## Licence

[MIT](LICENSE), as PeakCut. Caption fonts in `studio/fonts/` (Anton, Archivo Black, Poppins) are under the SIL Open
Font License; the licence texts are in the same folder.
