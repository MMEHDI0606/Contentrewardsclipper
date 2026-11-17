# Status and how to use it

Where Clipping Studio stands today, and the steps to go from a creator's stream to a paid Content Rewards
submission. For setup, see [README.md](README.md). For what to do when something breaks, see [RUNBOOK.md](RUNBOOK.md).

## Status (2026-11-17)

| Part | State |
|---|---|
| Clips from a pasted video | Working. Tested end to end with a stand-in brain: a 5-minute stretch gave 3 finished clips in 75 s |
| Live recording and clipping | Working. A pasted live link started recording in 20 s and made a clip |
| Claude Code brain (MCP server) | Working in its self-test. **Not yet run on real work**: first real run still to do |
| Campaigns, campaign check, tracker | Working in tests and the end-to-end run |
| Reading Content Rewards campaign pages | Working on public pages |
| Submitting to Content Rewards | **Built, not tested.** The submit form is only visible after logging in and joining a campaign |
| Playbooks and rotation | Working: 9 playbooks, 4 layouts |
| Edit effects | Working in test renders: cut pauses, punch-in zoom, freeze frame with slow-motion replay, streamer name tag. Set by the playbook, or by hand in the clip editor |
| Separate cuts per platform | Working in tests: TikTok and Reels 15–35 s, Shorts 30–60 s by default. Each platform is posted its own version |
| Results page and weekly review | Built. Needs 10 or more posted clips with views before the review runs |
| Posting to YouTube, Instagram, TikTok | From PeakCut, unchanged. Each account needs its one-time connection |
| Waiting on outside things | TikTok view counts (needs TikTok to approve reading views for the app), the recipe importer (needs a sample recipe from your edit tool) |

All 19 test groups pass (`python tests/run.py`), including the new ones for the edit effects and per-platform cuts.

### First real runs with the Claude Code brain (2026-11-17)

| Run | Result |
|---|---|
| Brain start | **Fixed a bug**: the brain started but could never call its two tools (Claude Code held them back behind tool search, which the brain is not given). `run-brain.ps1` now turns tool search off; the brain took its first request 16 s after restarting |
| A 20-minute TED talk pasted in the app | 4 clips, each with TikTok and Instagram cuts. The two picked by the brain are good (real hooks, Best line first and Long story, pauses cut 52 s → 45 s). The two picked while the brain was broken fell back to loudness and are poor (the TED intro) |
| A YouTube live channel link pasted in the app (Sky News) | **Fixed a bug**: channel links (`twitch.tv/name`, `kick.com/name`, `youtube.com/@name/live`) were refused as "a channel, not a video". Now a live channel is recorded; an offline one says it isn't live. Recorded 12 minutes; the brain checked the words 5 times and found nothing worth an automatic clip on a news stream; "Last 60s" made a clip with the brain's hook |
| A Twitch live channel link pasted in the app (AustinShow) | Recorded 7.5 minutes (stopped early from the window). The brain checked 5 times with nothing above the score bar; "Last 60s" made a 53 s clip with the brain's hook and context line, the Long story playbook with pauses cut, and 28 s TikTok and Instagram cuts |
| Brain speed | About one request a minute; 13 requests in the first hour, none skipped |

Still open from these runs: TikTok and Instagram share the same length range, so their cuts are the same file content;
set different ranges (`PEAKCUT_CUT_TIKTOK`, `PEAKCUT_CUT_INSTAGRAM`) if each platform must get a different file.

## Before the first real run

1. Run `start.cmd`. In the brain window, answer the trust question: down arrow, then Enter.
2. Check the bottom left of the dashboard says **Brain connected** with a green dot.
3. Make one clip from a short video (below), then open `/usage` in the brain window and your claude.ai usage
   settings. The brain's work should count against your normal plan limits. If it is being charged as Agent SDK
   credit instead, stop and tell whoever maintains this.

## Everyday use

### 1. Add the campaign

**Campaigns** in the left menu.

- Paste the campaign's link (`https://contentrewards.com/c/campaigns/...`) and press **Import**. The rate, payout
  cap, budget, platforms and status are read from the page.
- To find campaigns, press **Browse Discover**. To see the ones you've joined, press **My campaigns**. That needs you
  to log in first: press **Log in**, then type your own password and code in the window that opens.
- Join the campaign yourself on Content Rewards. The app never joins for you.
- Press **Edit** on the campaign to check the brief, the disclosure text (for example "#ad"), the required tags and
  text, and the clip length. Then choose what happens on its own:

| Setting | What happens |
|---|---|
| Off: nothing automatic | Nothing, until you start it |
| Record and clip, I review | Records and clips; you approve and post (**start here**) |
| Also post automatically | Clips that pass the check and score high enough are posted to one account |
| Also submit to Content Rewards | Posted clips are also submitted. Only after you've tested one submission by hand |

### 2. Link the creator

- After importing, the campaign card suggests creators to link. Press **Link to ...** for the right one, or
  **Create a workspace for ...** if they're new. Nothing is linked unless you press it.
- In the creator's **Setup** tab, check **Their stream** (their channel links), and under **Campaign and camera**
  pick the default campaign. If their streams show a face camera, enter its position as four percentages (left,
  top, width, height). This turns on the camera-over-gameplay and full-camera layouts.
- If a creator is on more than one campaign and has no default, the Campaigns page asks you to choose one. Their
  clips wait until you do.

### 3. Get clips

- **From a video:** on **Home**, paste the link and press **Make clips**.
- **From a live stream:** paste the live link in the same box, and recording starts by itself. You can also open
  the creator and press **Record live stream**.
- **Automatically:** creators linked to an active campaign that is set to record are recorded as soon as they go
  live. The **Watchlist** on the Campaigns page shows who is watched, and its priority numbers decide who goes first
  when several are live (lower goes first).
- While recording, **Last 60s** makes a clip of what just happened.

### 4. Review each clip

Open a clip from the creator's **Clips** tab or **All videos**.

- **Campaign check:** passed, or the reasons it failed. A failed clip can't be posted or submitted. Fix the clip
  and press **Check again**, or press **Override** if you're sure the check is wrong.
- **Playbook** and **Context line:** change them to present the clip differently, then make it again.
- **Cut pauses**, **Zoom in on the peak**, **Freeze and slow-motion replay:** the playbook sets these; tick or untick
  them and press **Save and re-make clip**.
- **Per-platform versions:** when the clip is too long or short for a platform, a separate version is made for it
  (for example "TikTok cut 28 s") and listed under the clip. Posting uses it automatically.
- Trim it, change the hook or the captions, as in PeakCut.
- Press **Approve** when it is good to go.

### 5. Post

- Connect accounts once in **Settings**, **Connected apps**. Pick which accounts each creator's clips go to in the
  creator's **Setup** tab, under **Posting**.
- Press **Add to queue** on a clip. A campaign clip goes to **one** account only. The campaign's disclosure, tags and
  required text are added to the end of the post text for you.
- **Pause all posting** on Home stops every automatic post at once. Recording and clipping carry on.

### 6. Submit to Content Rewards (within 30 minutes)

Content Rewards' terms require submitting within 30 minutes of posting. You get an alert when a clip is posted, and
another at 20 minutes if it hasn't been submitted.

- **By hand (for now):** copy the post link from the clip, submit it on Content Rewards, then set the clip's state to
  submitted and press **Save state**.
- **From the app:** press **Submit to Content Rewards** on the clip. If the page doesn't look as expected, it stops
  and saves a screenshot in `data/cr-shots/`. After the first one, check on the site that it arrived.
- These rules are enforced in code: one campaign per post, a rejected post is never resubmitted, and a clip that
  failed the check needs Override first.

### 7. Track payment

Each clip's state goes made, approved, posted, submitted, then paid or rejected. Enter the payable views shown on
Content Rewards on each clip; TikTok views aren't read by the app. Each campaign card shows clips, views and
estimated earnings after the 10% fee, capped per clip.

### 8. Learn from results

**Results** in the left menu.

- **Account health:** posts a day, and median views this week against last week. A sharp drop is flagged; slow
  down that account if it shows.
- **By playbook, length, hour and campaign:** see what works.
- **Weekly review:** once a week, the brain suggests changes. Press **Accept** or **Reject**. Nothing changes until
  you accept.

## Don'ts

- Don't set `ANTHROPIC_API_KEY` on this computer.
- Don't use more than one Content Rewards account, or resubmit a rejected post.
- Don't clip creators whose campaign you haven't joined.
- Don't turn on "Also submit to Content Rewards" until one submission has worked by hand.
- Don't expose the app to the internet. It is meant for this laptop only.
