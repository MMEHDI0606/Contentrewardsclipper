# Clipping Studio runbook

A local clipping app (a fork of PeakCut, MIT) whose AI work is done by a Claude Code session on this laptop (the
"brain"), using your Claude subscription. No API key is used anywhere.

## Start and stop

| What | How |
|---|---|
| Everything | double-click `start.cmd` (app server hidden, brain in its own window, dashboard in the browser) |
| App only, no AI | `start.cmd -NoBrain` |
| Push mode (experimental) | `start.cmd -Mode channel` (Claude Code channels, research preview) |
| Stop everything | `stop.cmd` |

The dashboard is <http://127.0.0.1:8765/app>. While the app runs it keeps the laptop awake (`scripts/run-server.ps1`
asks Windows not to sleep); the screen may still turn off. Windows itself is set never to sleep.

## Is the brain healthy?

- Bottom left of the dashboard: a green dot and "Brain connected". Red means it is not answering.
- A red banner at the top says the same, with what to do.
- AI usage page: calls today and estimated tokens (the session reports no counts; they are characters / 4).
- `GET /api/status` → `brain`: `connected`, `pending`, `working`, `per_hour`, `live_skipped`.

## When it is not healthy

1. **First start in a new folder**: the brain window (titled `claude`) asks "Do you trust the files in this folder?".
   The highlighted answer is **No, exit**: press the down arrow, then Enter. MCP tools do not load until you do.
   This is why the MCP server "did not start" on 2026-11-16.
2. Brain window closed or crashed: `run-brain.ps1` restarts it after 15 s (120 s if it exits at once, e.g. a login
   problem). Requests it had taken are offered again after 180 s, so nothing is lost.
3. Not logged in: in the brain window, `/login` with your claude.ai account. Make sure `ANTHROPIC_API_KEY` is not set.
4. Videos wait up to 10 minutes per request for the brain; live checks wait 2 minutes, then are skipped. With more
   than 4 requests waiting, the oldest live checks are skipped first (`PEAKCUT_BRAIN_BACKLOG`).
5. Logs: `run/server.log` (app), the brain window itself, `data/cr-shots/` (Content Rewards screenshots).

## Campaigns (Content Rewards)

- **Campaigns** page: paste a campaign link and press Import, or Browse Discover / My campaigns. Pages are read with
  Playwright in Edge on one saved profile (`data/cr-profile`). Public pages need no login; joined details
  (requirements) and submitting need you to log in once with **Log in** (a visible window; you type your own
  password and 2FA; nothing is stored by the app).
- After import, the app *suggests* creators to link (by channel link or name). Nothing is linked until you press it.
- Each creator can have one default campaign (creator → Setup → Campaign and camera). With several and no default,
  the Campaigns page asks you, showing rate, budget and brief side by side; clips wait as "needs a campaign".
- Every campaign clip carries the disclosure tag on screen from the first frame, and the campaign's required tags,
  text and disclosure are added last to the post caption.
- The campaign check runs when each clip finishes: rules in code (length, disclosure, campaign accepting) plus the
  brain reading the brief. A failed clip cannot enter the post queue or be submitted unless you press Override.
- What happens on its own is set per campaign: Off; Record and clip (default); also post; also submit.
  The global pause (Settings) stops all automatic posting at once.
- Rules from the Creator Terms enforced in code: one campaign per post, never resubmit a rejected post, submit within
  30 minutes of posting (you get a warning at 20 minutes), one account, no stealth, stop on CAPTCHA or block pages.
- Joining campaigns, payouts and withdrawals are never automated.

## Playbooks

`playbooks/*.json`, one per recipe (hook rules, edit, caption style, variants). The brain suggests one per clip;
code rotates them (no playbook and variant three times running; caption styles alternate). Built edit primitives:
full-frame crop, camera-over-gameplay split and full-cam (needs the camera box on the creator), result-first teaser,
context line, disclosure tag. Override any clip in its editor (Playbook, Context line) and re-make it.

## Results

The **Results** page groups posted clips by playbook, length, hour, category, platform, campaign, AI score and caption
style; shows account health (posts a day, median views this week vs last, reach drop); best and worst clips; and once
a week (10+ posted clips with views) the brain's suggestions, which you accept or reject. Nothing changes by itself.

## Measurements so far (2026-11-16)

| What | Result |
|---|---|
| Test suite | 16 suites pass (`python tests/run.py`, set `PYTHONUTF8=1` on Windows for the Hindi/Urdu suite) |
| A 5-minute stretch of a YouTube talk, stand-in brain | 75 s from link to 3 finished campaign clips; 8 brain requests (1 clip search, 3 caption fixes, 3 checks, 1 test) |
| Render time per clip (1080x1920, this laptop) | 5–9 s |
| Live link pasted (YouTube news stream), stand-in brain | workspace made, recording in 20 s, a clip rendered |
| Content Rewards campaign page read (public) | name, platforms, rate, payout cap, budget and status all correct |

The stand-in brain answered through the same `/api/brain/*` doors the MCP server uses; the MCP server itself was
checked separately (`node brain/selftest.mjs`).

## Not verified yet (needs you, or real use)

1. **Billing**: after the first real video and the first hour of live with the real brain, check usage in Claude Code
   (`/usage`) and claude.ai settings. Brain turns should count against normal plan limits, not the Agent SDK credit.
   If they hit the credit, stop and use Ollama for live (Settings, AI).
2. **Clip quality and context growth with the real brain**: not run yet (you asked not to start it). Measure how fast
   the brain's context fills over a long video and a long stream; restart it per stream if needed.
3. **Live capacity of this laptop** (how many streams at once) and brain requests per stream hour.
4. **Content Rewards submission form**: it is only visible when logged in and joined, so the submit step looks for a
   button named "Submit" and one link field, and stops with a screenshot if the page differs. Test it once by hand
   with autonomy below "submit" first.
5. **Joined-campaign list** (My campaigns) reads `/c` when logged in; not seen yet.
6. **TikTok views**: not read (the TikTok connection only has publishing rights). Enter payable views from Content
   Rewards on each clip instead.
7. **Reach under Instagram and YouTube originality rules**: watch Account health from the first week.
8. **How many social accounts Content Rewards allows per creator**.

## Changes from the build spec, and why

- **Watch interval** defaults to 2 minutes (`PEAKCUT_WATCH_EVERY_MIN`), not 15: the existing watcher already ran every
  2 minutes server-side (not only with the page open), and a status check is cheap.
- **Campaign pages are read with fixed text rules**, not the AI: the page structure is regular, and no AI is needed.
- **Camera box** is typed as four percentages on the creator, not drawn in the UI.
- **Not built**: the importer for your edit-recipe tool (send its recipe schema first; the spec says not to guess it).
- **Edit effects** (cut pauses, punch-in zoom, freeze frame with slow-motion replay) are switched on by playbooks:
  Long story and Micro cut pauses, Chat erupts punches in, Fail replay freezes and replays. The punch and replay land
  on the last highlighted word, or a second before the end. Pauses after the last word are kept (the reaction).
- **UI renamed** to Clipping Studio with its own palette and logo. Environment variables keep the `PEAKCUT_` prefix,
  and the original MIT licence stays.
