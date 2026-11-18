"""Post queue: decides when each finished clip goes to which account, posts it, and tracks views."""
import secrets
import threading
import time
from pathlib import Path

import json

from . import accounts, alerts, campaigns, config, creators, ctx, jobs, live

_lock = threading.RLock()
_FILE = config.DATA_DIR / "posts.json"
try:
    _posts: list = json.loads(_FILE.read_text(encoding="utf-8")) if _FILE.exists() else []
except ValueError:
    _posts = []


def _mine() -> list:
    u = ctx.uid()
    return [p for p in _posts if p.get("user") == u]
OPEN = ("scheduled", "posting")


def _save():
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_posts, indent=1), encoding="utf-8")
        tmp.replace(_FILE)


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _mirror(p):
    """Copy the post's state onto its clip so the clip card can show it."""
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    if clip is not None:
        clip.setdefault("posts", {})[p["id"]] = {k: p.get(k) for k in ("account_name", "platform", "status", "url", "due_at", "error")}
        jobs.save(job)


def _next_slot(account_id, gap_min, cap):
    """Earliest time that respects the gap between posts and the daily cap for this account."""
    mine = [p for p in _mine() if p["account"] == account_id and p["status"] in OPEN + ("posted",)]
    t = max(time.time(), max((p.get("posted_at") or p["due_at"] for p in mine), default=0) + gap_min * 60)
    for _ in range(30):
        if sum(1 for p in mine if _day(p.get("posted_at") or p["due_at"]) == _day(t)) < cap:
            break
        lt = time.localtime(t + 86400)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1))  # 9:00 next day
    return t


def enqueue(job, clip, account_ids, now=False) -> list:
    c = creators.get(job["creator"]) or creators.DEFAULTS
    why = campaigns.postable(clip)
    if why:
        raise ValueError(why)
    if clip.get("campaign"):
        # a campaign clip goes to one account per platform: the same file on two accounts of one platform looks like
        # reposted content
        used = {p["account"] for p in _mine() if p["job"] == job["id"] and p["clip"] == clip["id"] and p["status"] in OPEN + ("posted",)}
        if used and set(account_ids) <= used:
            return []
        plat = lambda aid: (accounts.get(aid) or {}).get("platform")  # noqa: E731
        want = [plat(a) for a in account_ids if a not in used]
        if len(want) != len(set(want)) or set(want) & {plat(a) for a in used}:
            raise ValueError("A campaign clip is posted to one account per platform. Pick a single account for each.")
        clip["approved"] = True
    made = []
    with _lock:
        for aid in account_ids:
            acc = accounts.get(aid)
            if not acc:
                continue
            if any(p["job"] == job["id"] and p["clip"] == clip["id"] and p["account"] == aid
                   and p["status"] in OPEN + ("posted",) for p in _mine()):
                continue  # never post the same clip to the same account twice
            due = time.time() if now else _next_slot(aid, c["post_gap_min"], c["daily_cap"])
            p = {"id": secrets.token_hex(8), "user": ctx.uid(), "job": job["id"], "clip": clip["id"], "creator": job["creator"],
                 "account": aid, "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                 "due_at": due, "created": time.time(), "title": clip.get("title") or clip.get("hook") or "Clip",
                 "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None}
            _posts.append(p)
            made.append(p)
        _save()
    for p in made:
        _mirror(p)
    return made


def on_clip_ready(job, clip):
    """Called once when a clip first finishes rendering."""
    if clip.get("campaign"):
        threading.Thread(target=ctx.bind(campaign_clip_ready), args=(job, clip), daemon=True).start()
        return
    c = creators.get(job["creator"])
    if not c or not c["autopost"] or not c["accounts"]:
        return
    if clip.get("score", 0) < c["post_min_score"]:
        return  # includes hand-made clips (score 0): those wait for you to press Post
    made = enqueue(job, clip, c["accounts"])
    if made:
        creators.log(c["id"], f"Queued for {len(made)} account(s): {clip.get('title') or clip.get('hook')}")


def campaign_clip_ready(job, clip):
    """A campaign clip is finished: check it against the brief, then do what the campaign allows on its own."""
    clip["compliance"] = campaigns.check(job, clip)
    jobs.save(job)
    camp = campaigns.get(clip["campaign"])
    if not camp:
        return
    name = clip.get("title") or clip.get("hook") or clip["id"]
    if not clip["compliance"]["ok"]:
        creators.log(job.get("creator"), f"Campaign check failed for {name}: {'; '.join(clip['compliance']['reasons'])[:200]}")
        alerts.notify(f"a clip for {camp['name']} needs a look: it failed the campaign check ({name}).", kind="post")
        return
    if camp["autonomy"] in ("post", "submit") and clip.get("score", 0) >= camp["post_min_score"] and not accounts.settings()["paused"]:
        c = creators.get(job.get("creator") or job.get("source_creator")) or {}
        ok_platforms = camp["platforms"] or list(accounts.PLATFORM_NAMES)
        accts = {}
        for a in c.get("accounts", []):          # the first account of each platform the campaign takes
            if (accounts.get(a) or {}).get("platform") in ok_platforms:
                accts.setdefault(accounts.get(a)["platform"], a)
        if accts:
            try:
                made = enqueue(job, clip, list(accts.values()))
                if made:
                    creators.log(job.get("creator"), f"Queued for {made[0]['account_name']} ({camp['name']}): {name}")
            except ValueError as e:
                creators.log(job.get("creator"), f"Not queued: {e}")


def _caption(clip, creator):
    tags = clip.get("hashtags", [])
    camp = campaigns.get(clip.get("campaign"))
    extra = []
    if camp:       # the campaign's required text, tags and disclosure come last and are never dropped
        tags = [t for t in tags if t.lower() not in {x.lower() for x in camp["hashtags"]}][:5] + camp["hashtags"]
        extra = [camp["required_text"], camp["disclosure"]]
    tags = " ".join("#" + h for h in tags)
    return "\n\n".join(x for x in [clip.get("description", ""), tags, creator.get("post_footer", "")] + extra if x)


def _post(p):
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    acc = accounts.get(p["account"])
    try:
        if not clip or clip.get("status") != "done":
            raise RuntimeError("The clip no longer exists or is not finished.")
        if not acc:
            raise RuntimeError("That account was disconnected.")
        path = Path(job["dir"]) / "clips" / clip["file"]
        cut = (clip.get("cuts") or {}).get(acc["platform"])     # the version made for this platform's length
        if cut and (path.parent / cut["file"]).is_file():
            path = path.parent / cut["file"]
        creator = creators.get(job["creator"]) or dict(creators.DEFAULTS, name="clips")
        title, cap = clip.get("title") or clip.get("hook") or "Clip", _caption(clip, creator)
        if acc["platform"] == "youtube":
            rid, url = accounts.youtube_upload(acc, path, title, cap + "\n\n#Shorts", clip.get("hashtags", []), creator["privacy"])
        elif acc["platform"] == "instagram":
            rid, url = accounts.instagram_upload(acc, path, f"{title}\n\n{cap}", job["id"])
        elif acc["platform"] == "tiktok":
            rid, url = accounts.tiktok_upload(acc, path, f"{title}\n\n{cap}", creator["privacy"])
        else:
            raise RuntimeError("That kind of account is no longer supported.")
        p.update(status="posted", posted_at=time.time(), remote_id=rid, url=url, error=None)
        creators.log(p["creator"], f"Posted to {acc['name']}: {title}")
        if clip.get("campaign"):
            campaigns.track(clip, "posted", post_url=url, account=acc["name"], platform=acc["platform"])
            jobs.save(job)
            camp = campaigns.get(clip["campaign"])
            alerts.notify(f"posted to {acc['name']} for {camp['name'] if camp else 'a campaign'}: submit it within 30 minutes ({title}).", kind="post")
            if camp and camp["autonomy"] == "submit":
                from . import cr
                cr.queue_submit(job["id"], clip["id"])
    except Exception as e:  # noqa: BLE001
        p["tries"] += 1
        p["error"] = str(e)[-300:]
        if p["tries"] < 3 and clip and acc:
            p.update(status="scheduled", due_at=time.time() + 600)  # try again in 10 minutes
        else:
            p["status"] = "failed"
            creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
            alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
    _save()
    _mirror(p)


def _run():
    while True:
        time.sleep(15)
        due = [p for p in list(_posts) if p["status"] == "scheduled" and p["due_at"] <= time.time() and p.get("user")]
        for p in due:
            try:
                with ctx.as_user(p["user"]):
                    if accounts.settings()["paused"]:
                        continue
                    with _lock:
                        if p["status"] != "scheduled":
                            continue
                        p["status"] = "posting"
                    _post(p)
            except Exception:  # noqa: BLE001
                pass


SNAPS = (("1h", 3600), ("6h", 6 * 3600), ("24h", 86400), ("3d", 3 * 86400), ("7d", 7 * 86400))


def _snapshot(recent):
    """Views at about 1 hour, 6 hours, 1 day, 3 days and 7 days after posting: the curve, not just the total."""
    for p in recent:
        if p.get("views") is None or not p.get("posted_at"):
            continue
        age = time.time() - p["posted_at"]
        snaps = p.setdefault("snaps", {})
        for label, sec in SNAPS:
            if age >= sec and label not in snaps:
                snaps[label] = p["views"]


def _stats_for(recent):
    for aid in {p["account"] for p in recent if p["platform"] == "youtube"}:
        acc = accounts.get(aid)
        if acc:
            views = accounts.youtube_views(acc, [p["remote_id"] for p in recent if p["account"] == aid])
            for p in recent:
                if p["account"] == aid and p["remote_id"] in views:
                    p["views"] = views[p["remote_id"]]
    for p in recent:
        acc = accounts.get(p["account"]) if p["platform"] == "instagram" else None
        if acc:
            v = accounts.instagram_views(acc, p["remote_id"])
            if v is not None:
                p["views"] = v


KEEP_POSTED = 86400     # a clip's video files are deleted this long after its last post went up


def _tidy():
    """Free the drive: a day after a clip's last post went up, with nothing of it still waiting or failed, delete its
    video files. The post stays up, and the clip's record, cover picture, links and views stay for Results."""
    by_clip = {}
    for p in _mine():
        by_clip.setdefault((p["job"], p["clip"]), []).append(p)
    for (jid, cid), ps in by_clip.items():
        up = [p.get("posted_at") or 0 for p in ps if p["status"] == "posted"]
        if not up or any(p["status"] in OPEN + ("failed",) for p in ps) or time.time() - max(up) < KEEP_POSTED:
            continue
        job = jobs.get(jid)
        clip = next((c for c in job["clips"] if c["id"] == cid), None) if job else None
        if not clip or clip.get("files_deleted"):
            continue
        for name in [clip.get("file")] + [c.get("file") for c in (clip.get("cuts") or {}).values()]:
            if name:
                (Path(job["dir"]) / "clips" / Path(str(name)).name).unlink(missing_ok=True)
        clip["files_deleted"] = time.time()
        jobs.save(job)
        ctx.bump()


def _stats():
    while True:
        for uid in {p.get("user") for p in _posts if p.get("user")}:
            try:
                with ctx.as_user(uid):
                    recent = [p for p in _mine() if p["status"] == "posted" and time.time() - p["posted_at"] < 14 * 86400]
                    _stats_for(recent)
                    _snapshot(recent)
                    _tidy()
            except Exception:  # noqa: BLE001
                pass
        _save()
        time.sleep(1800)


def cancel(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] != "scheduled":
            raise ValueError("Only posts that are still waiting can be cancelled.")
        p["status"] = "cancelled"
        _save()
    _mirror(p)


def retry(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled posts can be retried.")
        p.update(status="scheduled", due_at=time.time(), tries=0, error=None)
        _save()
    _mirror(p)


def overview() -> dict:
    today = _day(time.time())
    _posts = _mine()  # everything below is about the signed-in user only
    rows = []
    all_cs, all_js = creators.all_creators(), jobs.list_jobs()
    for c in all_cs:
        js = [j for j in all_js if j["creator"] == c["id"]]
        clips = [k for j in js for k in j["clips"] if k.get("status") == "done"]
        ps = [p for p in _posts if p["creator"] == c["id"]]
        views = sum(p["views"] or 0 for p in ps)
        rows.append({
            "id": c["id"], "name": c["name"],
            "state": "recording" if live.active_for(c["id"]) else "waiting" if c["auto_watch"] and c["channel"] else "idle",
            "autopost": c["autopost"] and bool(c["accounts"]),
            "clips_today": sum(1 for k in clips if _day(k.get("made_at", 0)) == today), "clips_total": len(clips),
            "posted_today": sum(1 for p in ps if p["status"] == "posted" and _day(p["posted_at"]) == today),
            "posted_total": sum(1 for p in ps if p["status"] == "posted"),
            "queued": sum(1 for p in ps if p["status"] in OPEN), "failed": sum(1 for p in ps if p["status"] == "failed"),
            "views": views, "earnings": round(views / 1000 * c["rate_per_1k"], 2)})
    names = {c["id"]: c["name"] for c in all_cs}
    queue = sorted((p for p in _posts if p["status"] in OPEN), key=lambda p: p["due_at"])
    done = sorted((p for p in _posts if p["status"] in ("posted", "failed")), key=lambda p: -(p.get("posted_at") or p["due_at"]))[:15]
    show = [{**{k: v for k, v in p.items() if k != "user"}, "creator_name": names.get(p["creator"], "")} for p in queue + done]
    # last 14 days, oldest first, for the home graph
    days = [_day(time.time() - 86400 * i) for i in range(13, -1, -1)]
    made, posted, views = {d: 0 for d in days}, {d: 0 for d in days}, {d: 0 for d in days}
    for j in all_js:
        for k in j["clips"]:
            d = _day(k.get("made_at") or j["created"])
            if k.get("status") == "done" and d in made:
                made[d] += 1
    for p in _posts:
        if p["status"] == "posted" and _day(p["posted_at"]) in posted:
            posted[_day(p["posted_at"])] += 1
            views[_day(p["posted_at"])] += p["views"] or 0
    daily = [{"day": d, "clips": made[d], "posted": posted[d], "views": views[d]} for d in days]
    return {"paused": accounts.settings()["paused"], "creators": rows, "posts": show, "daily": daily,
            "alerts": alerts.configured(), "recording": len(live.active_mine())}


def digest_text() -> str:
    o = overview()
    lines = ["Clipping Studio daily summary"]
    for r in o["creators"]:
        lines.append(f"- {r['name']}: {r['state']}, {r['clips_today']} clips today, {r['posted_today']} posted today, "
                     f"{r['queued']} waiting, {r['failed']} failed, {r['views']} views")
    if not o["creators"]:
        lines.append("No creators set up yet.")
    if o["paused"]:
        lines.append("Posting is paused.")
    return "\n".join(lines)


def start():
    with _lock:
        for p in _posts:
            if p["status"] == "posting":  # interrupted mid-upload last time
                p.update(status="failed", error="The studio closed during this upload. Check the account, then retry.")
        _save()
    jobs.on_clip_ready.append(on_clip_ready)
    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_stats, daemon=True).start()
    threading.Thread(target=alerts.digest_loop, args=(digest_text,), daemon=True).start()
    threading.Thread(target=alerts.keep_awake_loop,
                     args=(lambda: bool(live.active()) or any(p["status"] in OPEN for p in _posts),), daemon=True).start()
