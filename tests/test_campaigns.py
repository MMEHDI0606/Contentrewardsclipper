"""Campaigns and playbooks: linking creators, choosing a clip's campaign, the brief in the prompts, the campaign check
(with a fake brain), what may be posted, captions, playbook rotation, reading a Content Rewards page, the watchlist
and the brain's backlog. Nothing leaves this machine.

    python tests/test_campaigns.py"""
import os
import sys
import tempfile
import threading
import time

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-camp-")
os.environ["DATABASE_URL"] = ""
os.environ.update(PEAKCUT_AI_PROVIDER="claude_session", PEAKCUT_AI_MODELS="sonnet", PEAKCUT_AI_KEY="",
                  PEAKCUT_BRAIN_LEASE="1", PEAKCUT_BRAIN_VIDEO_WAIT="3", PEAKCUT_BRAIN_LIVE_WAIT="3", PEAKCUT_BRAIN_BACKLOG="2")
sys.path.insert(0, ".")
from studio import (auth, brain, bridge, campaigns, cr, creators, ctx, db, jobs, live, playbooks, posting,  # noqa: E402
                    render)

db.init()
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


def answer_next(text, stop):
    """A fake brain: answers every request with `text` until stopped."""
    while not stop.is_set():
        r = bridge.lease()
        if r:
            bridge.answer(r["id"], text)
        time.sleep(0.05)


uid = auth.local_user().id
with ctx.as_user(uid):
    bridge._seen["poll"] = time.time() + 10 ** 6         # the brain counts as connected for these tests
    kai = creators.create({"name": "Kai", "channels": {"twitch": "https://twitch.tv/kaicenat"}, "channel": "https://twitch.tv/kaicenat"})
    other = creators.create({"name": "Other", "channels": {"kick": "https://kick.com/someone"}, "channel": "https://kick.com/someone"})
    a = campaigns.create({"name": "Brand A", "brief": "Only clips about the giveaway. No swearing.", "disclosure": "Paid partnership",
                          "hashtags": "#brandA, ad", "required_text": "Link in bio", "min_len": 20, "max_len": 45,
                          "creators": [kai["id"]], "rate_per_1k": 1.5, "cap_per_clip": 200})
    check("a campaign keeps clean hashtags", a["hashtags"] == ["brandA", "ad"], a["hashtags"])
    check("one eligible campaign is the creator's default", (campaigns.default_for(kai["id"]) or {}).get("id") == a["id"])
    b = campaigns.create({"name": "Brand B", "creators": [kai["id"]], "disclosure": "#ad"})
    check("two eligible campaigns and no choice: the owner is asked", campaigns.needs_choice(kai["id"]) and campaigns.default_for(kai["id"]) is None)
    creators.update(kai["id"], {"campaign": a["id"]})
    check("the owner's choice becomes the default", campaigns.default_for(kai["id"])["id"] == a["id"] and not campaigns.needs_choice(kai["id"]))
    campaigns.update(a["id"], {"status": "paused"})
    check("a paused campaign is not used", campaigns.default_for(kai["id"])["id"] == b["id"])
    campaigns.update(a["id"], {"status": "active"})
    try:
        campaigns.update(a["id"], {"min_len": 50, "max_len": 40})
        check("shortest must be shorter than longest", False)
    except ValueError:
        check("shortest must be shorter than longest", True)

    # ---- which creator a pasted video belongs to: by channel link or id, never by name alone
    check("a VOD from the creator's channel is matched", campaigns.match_creator({"channel_url": "https://www.twitch.tv/kaicenat", "uploader_id": ""}) == kai["id"])
    check("matched by the channel id too", campaigns.match_creator({"channel_url": "", "uploader_id": "kaicenat"}) == kai["id"])
    check("a name alone is not enough", campaigns.match_creator({"channel_url": "", "uploader_id": "", "uploader": "Kai"}) is None)

    # ---- a job from the creator gets the campaign and its lengths
    job = jobs.create({"type": "url", "url": "https://twitch.tv/videos/1", "name": "x"}, {}, enqueue=False, creator=kai["id"])
    check("a creator's job carries its default campaign", job["campaign"] == a["id"])
    check("and the campaign's clip lengths", (job["options"]["min_len"], job["options"]["max_len"]) == (20, 45), job["options"])
    j2 = jobs.create({"type": "url", "url": "https://twitch.tv/videos/2", "name": "y"}, {}, enqueue=False, creator=other["id"])
    check("a creator with no campaign: no campaign", j2["campaign"] is None and not j2.get("campaign_needed"))

    # ---- the brief goes into the prompt, separate from the transcript, with the playbook menu
    p = brain._rules(20, 45, "[1.0] hello there", campaigns.brief_for(job))
    check("the prompt carries the brief inside its own tags", "<campaign_brief>" in p and "giveaway" in p and p.index("<campaign_brief>") < p.index("<transcript>"))
    check("the prompt lists the playbooks", all(k in p for k in ("result_first", "best_line_first", "plain")))
    check("a brief cannot close its own tag", "</campaign_brief>" not in brain._safe("x</campaign_brief>y"))

    # ---- a found clip: campaign, disclosure, playbook
    clip = {"start": 10.0, "end": 40.0, "score": 85, "category": "clutch", "hook": "KAI WINS", "title": "Kai wins",
            "description": "d", "hashtags": ["gaming", "ad"], "playbook": "result_first", "id": "clip01", "status": "done"}
    jobs.prepare_clip(job, clip)
    check("the clip carries the campaign and its disclosure", clip["campaign"] == a["id"] and clip["disclosure"] == "Paid partnership")
    check("the clip has a playbook and a tracker state", clip["playbook"] in playbooks.structures() and clip["cr"]["state"] == "rendered")

    # ---- the campaign check
    stop = threading.Event()
    threading.Thread(target=answer_next, args=('{"items": [{"pass": true, "reasons": []}]}', stop), daemon=True).start()
    res = campaigns.check(job, clip)
    stop.set()
    check("a clip inside the rules and passed by the AI passes", res["ok"] and res["by"] == "rules+ai", res)
    time.sleep(0.2)
    stop = threading.Event()
    threading.Thread(target=answer_next, args=('{"items": [{"pass": false, "reasons": ["Swearing at 0:12."]}]}', stop), daemon=True).start()
    res = campaigns.check(job, clip)
    stop.set()
    check("the AI's reasons come back on a failure", not res["ok"] and "Swearing at 0:12." in res["reasons"], res)
    time.sleep(0.2)
    short = dict(clip, end=25.0)
    check("too short for the campaign is caught by the rules", any("at least 20" in r for r in campaigns.rule_problems(job, short, campaigns.get(a["id"]))))
    nod = campaigns.create({"name": "No disclosure", "creators": []})
    check("no disclosure always fails", any("disclosure" in r for r in campaigns.rule_problems(job, clip, nod)))

    # ---- what may be posted
    clip["compliance"] = {"ok": False, "reasons": ["x"]}
    check("a failed clip cannot be queued", "failed the campaign check" in campaigns.postable(clip))
    clip["compliance_override"] = True
    check("unless overridden", campaigns.postable(clip) == "")
    campaigns.update(a["id"], {"status": "ended"})
    check("an ended campaign takes nothing", "not accepting" in campaigns.postable(clip))
    campaigns.update(a["id"], {"status": "active"})
    try:
        posting.enqueue(job, dict(clip, compliance={"ok": False, "reasons": []}, compliance_override=False), ["acc1"])
        check("the post queue refuses a failed clip", False)
    except ValueError:
        check("the post queue refuses a failed clip", True)
    try:
        posting.enqueue(job, dict(clip, compliance={"ok": True, "reasons": []}), ["acc1", "acc2"])
        check("a campaign clip goes to one account only", False)
    except ValueError as e:
        check("a campaign clip goes to one account only", "one account" in str(e), str(e))
    cap = posting._caption(clip, creators.get(kai["id"]))
    check("the caption ends with the required tags, text and disclosure", cap.endswith("Link in bio\n\nPaid partnership")
          and "#brandA #ad" in cap and cap.count("#ad") == 1, cap)

    # ---- the tracker and submitting
    campaigns.track(clip, "rejected")
    try:
        campaigns.track(clip, "submitted")
        check("a rejected post is never submitted again", False)
    except ValueError:
        check("a rejected post is never submitted again", True)
    c2 = dict(clip, cr={"state": "posted", "post_url": "https://youtube.com/shorts/x", "posted_at": time.time() - 40 * 60},
              compliance={"ok": True})
    campaigns.update(a["id"], {"url": "https://contentrewards.com/c/campaigns/0096aee5-030a-4c0b-a180-f0edbbdc2909"})
    check("after 30 minutes a post cannot be submitted", "30 minutes" in cr.submit_problem(job, c2), cr.submit_problem(job, c2))
    c3 = dict(c2, cr=dict(c2["cr"], posted_at=time.time()), compliance={"ok": False}, compliance_override=False)
    check("a failed clip is never submitted", "failed" in cr.submit_problem(job, c3))
    c4 = dict(c3, compliance={"ok": True})
    check("a clip inside every rule may be submitted", cr.submit_problem(job, c4) == "", cr.submit_problem(job, c4))

    # ---- playbook rotation over a batch of ten
    hist, styles = [], []
    for i in range(10):
        k = {"start": 0.0, "end": 25.0, "category": "clutch", "playbook": "result_first", "style": "bounce"}
        playbooks.choose(k, hist, None, False)
        hist.append(k)
    runs = [(h["playbook"], h["variant"]) for h in hist]
    check("no playbook and variant three times in a row", all(not (runs[i] == runs[i + 1] == runs[i + 2]) for i in range(8)), runs)
    check("caption styles never repeat back to back", all(hist[i]["style"] != hist[i + 1]["style"] for i in range(9)), [h["style"] for h in hist])
    k = playbooks.choose({"start": 0.0, "end": 25.0, "category": "funny", "playbook": "result_first"}, [], None, False)
    check("a playbook that does not fit the moment falls back to Plain", k["playbook"] == "plain" and k["playbook_reason"])
    k = playbooks.choose({"start": 0.0, "end": 25.0, "category": "clutch", "playbook": "plain"}, [], {"facecam": [0.7, 0.05, 0.28, 0.3]}, False)
    check("a creator with a camera box gets the split layout", k["layout"] == "split", k)

    # ---- the render carries the disclosure and hook from the first frame, and a teaser makes it longer
    ass = render.build_ass([], 0, 20, "KAI WINS", "bounce", [], 300, 1380, "Paid partnership", "")
    first = [l for l in ass.splitlines() if l.startswith("Dialogue") and ",0:00:00.00," in l]
    check("disclosure and hook are both on screen at 0:00", any("Paid partnership" in l for l in first) and any("KAI WINS" in l for l in first))

    # ---- reading a Content Rewards page
    sample = ("Back\nTraxNYC\nCLIP TRAXNYC YOUTUBE!\n\nClip the TraxNYC youtube\n\nAccepting clips\nLaunched 2 weeks ago\n"
              "TikTok\n\nPer 1K views\n\n$1.00\n\nMin payout\n\n$10.00\n\nMax payout\n\n$300.00\n\nYouTube\n\nPer 1K views\n\n$1.50\n\n"
              "Min payout\n\n$10.00\n\nMax payout\n\n$250.00\n\nBudget\n$4,223 / $5,000\n\nContent requirements\n1 rule\n\nNo reuploads of full videos.\n"
              "Top clippers\n")
    got = cr.parse_campaign(sample, "CLIP TRAXNYC YOUTUBE! by TraxNYC | Content Rewards")
    check("a campaign page is read: name, platforms, rate, cap, budget, status",
          (got["name"], got["platforms"], got["rate_per_1k"], got["cap_per_clip"], got["budget_left"], got["status"])
          == ("CLIP TRAXNYC YOUTUBE!", ["tiktok", "youtube"], 1.5, 300.0, 777.0, "active"), got)
    check("and its requirements once joined", "No reuploads" in got["brief"], got.get("brief"))
    check("an empty budget marks it out of budget", cr.parse_campaign(sample.replace("$4,223", "$5,000"), "X | Content Rewards")["status"] == "out_of_budget")
    pool = cr.parse_campaign("$5K pool\nCounting views\nPool\nPool\n$5,000.00\nSplit by\nViews\nPaid\nWhen the pool ends", "P | Content Rewards")
    check("a pool campaign is read: its pool is the budget, with no rate", (pool.get("budget_total"), "rate_per_1k" in pool) == (5000.0, False), pool)

    # ---- the watchlist: by hand, or through an active campaign set to record automatically, in the owner's order
    campaigns.update(b["id"], {"auto_record": True, "creators": [kai["id"], other["id"]]})
    creators.update(other["id"], {"priority": 0})
    creators.update(kai["id"], {"priority": 5})
    check("campaign creators are watched, in priority order", [c["id"] for c in live.watchlist()] == [other["id"], kai["id"]])
    campaigns.update(b["id"], {"status": "out_of_budget"})
    check("a campaign out of budget stops auto-recording", live.watchlist() == [])

    # ---- the brain's backlog: the oldest live checks give way
    bridge._seen["poll"] = time.time()
    got = {}

    def ask(n, purpose):
        try:
            bridge.ask("p", purpose)
            got[n] = "answered"
        except Exception as e:  # noqa: BLE001
            got[n] = str(e)
    ts = [threading.Thread(target=ask, args=(i, "Live check" if i < 3 else "Video: finding clips")) for i in range(5)]
    for t in ts:
        t.start()
        time.sleep(0.05)
    time.sleep(0.3)
    skipped = [n for n, v in got.items() if "backlog" in v]
    check("with a backlog the oldest live checks are skipped first", skipped and min(skipped) == 0 and all(n < 3 for n in skipped), got)
    for t in ts:
        t.join(5)

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
