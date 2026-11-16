"""Campaigns: the paid clipping campaigns (for example on Content Rewards) that clips are made for.

A campaign has a brief and rules (lengths, required text and hashtags, a paid-partnership disclosure), and is linked
to the creators whose streams and videos it pays for. Each creator can have one default campaign, chosen by the owner,
never by the app. Every clip made from a linked creator carries that campaign: its brief shapes which moments are
picked, its rules are checked on the finished clip, and its state is tracked from made to paid.

Stored per user in users/<user>/campaigns.json."""
import copy
import json
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from . import config, ctx

_lock = threading.RLock()
_mem: dict = {}                    # user -> [campaign]

STATUSES = ("active", "paused", "out_of_budget", "ended")
AUTONOMY = ("off", "clip", "post", "submit")        # off; record and clip (you review); also post; also submit
PLATFORMS = ("youtube", "instagram", "tiktok")
TRACK = ("rendered", "approved", "posted", "submitted", "paid", "rejected")
DEFAULTS = {"name": "", "brief": "", "url": "", "platforms": [], "required_text": "", "hashtags": [], "disclosure": "",
            "min_len": 15, "max_len": 60, "rate_per_1k": 0.0, "cap_per_clip": 0.0, "budget_total": 0.0,
            "budget_left": 0.0, "status": "active", "autonomy": "clip", "auto_record": False, "creators": [],
            "footage_rules": "", "notes": "", "post_min_score": 80, "source": "manual", "cr_id": ""}
NUMBERS = {"min_len": (5, 180, int), "max_len": (10, 180, int), "post_min_score": (0, 100, int),
           "rate_per_1k": (0, 1000, float), "cap_per_clip": (0, 100000, float), "budget_total": (0, 10**7, float),
           "budget_left": (0, 10**7, float)}
TEXTS = {"name": 120, "brief": 8000, "url": 500, "required_text": 300, "disclosure": 80, "footage_rules": 1000,
         "notes": 2000, "source": 20, "cr_id": 80}


def _file():
    return config.user_dir(ctx.uid()) / "campaigns.json"


def _mine() -> list:
    u = ctx.uid()
    if u not in _mem:
        f = _file()
        try:
            got = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        except ValueError:
            got = []
        _mem[u] = [{**DEFAULTS, **c} for c in got if isinstance(c, dict) and c.get("id")]
    return _mem[u]


def _save():
    with _lock:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(_mine(), indent=1), encoding="utf-8")
        tmp.replace(f)
    ctx.bump()


def _clean(data: dict) -> dict:
    from . import creators
    out = {}
    for k, v in (data if isinstance(data, dict) else {}).items():
        if k not in DEFAULTS or v is None:
            continue
        if k in NUMBERS:
            lo, hi, kind = NUMBERS[k]
            v = max(lo, min(hi, kind(float(v or 0))))
        elif k in TEXTS:
            v = str(v).strip()[:TEXTS[k]]
            if k == "url" and v and not re.match(r"^https?://", v):
                raise ValueError("The campaign link must start with http:// or https://")
        elif k == "platforms":
            v = [p for p in (v if isinstance(v, list) else []) if p in PLATFORMS]
        elif k == "hashtags":
            items = v if isinstance(v, list) else re.split(r"[\s,]+", str(v))
            v = [t for t in (re.sub(r"[^\w]", "", str(x)) for x in items) if t][:15]
        elif k == "status":
            v = v if v in STATUSES else "active"
        elif k == "autonomy":
            v = v if v in AUTONOMY else "clip"
        elif k == "auto_record":
            v = bool(v)
        elif k == "creators":
            v = [str(x) for x in (v if isinstance(v, list) else []) if creators.get(str(x))][:50]
        out[k] = v
    return out


def all_campaigns() -> list:
    return copy.deepcopy(_mine())


def get(cid):
    c = next((c for c in _mine() if c["id"] == cid), None) if cid else None
    return copy.deepcopy(c) if c else None


def create(data: dict) -> dict:
    c = {**DEFAULTS, **_clean(data)}
    if not c["name"]:
        raise ValueError("Give the campaign a name.")
    if c["min_len"] >= c["max_len"]:
        raise ValueError("Shortest clip must be shorter than longest clip.")
    c.update(id="cmp-" + secrets.token_hex(4), created=time.time())
    with _lock:
        _mine().append(c)
        _save()
    return copy.deepcopy(c)


def update(cid, data: dict) -> dict:
    with _lock:
        c = next((c for c in _mine() if c["id"] == cid), None)
        if not c:
            raise KeyError(cid)
        new = {**c, **_clean(data)}
        if new["min_len"] >= new["max_len"]:
            raise ValueError("Shortest clip must be shorter than longest clip.")
        c.update(new)
        _save()
        return copy.deepcopy(c)


def delete(cid) -> bool:
    from . import creators
    with _lock:
        before = len(_mine())
        _mine()[:] = [c for c in _mine() if c["id"] != cid]
        if len(_mine()) == before:
            return False
        _save()
    for cr in creators.all_creators():
        if cr.get("campaign") == cid:
            creators.update(cr["id"], {"campaign": ""})
    return True


# ---------------------------------------------------------------- which campaign a clip belongs to
def accepting(c) -> bool:
    return bool(c) and c["status"] == "active"


def for_creator(creator_id) -> list:
    """Campaigns linked to this creator that are taking submissions."""
    return [copy.deepcopy(c) for c in _mine() if creator_id in c["creators"] and accepting(c)]


def default_for(creator_id):
    """The campaign this creator's clips go to: the one the owner chose, or the only eligible one. None when there
    are several and none was chosen (the owner is asked), or none at all."""
    from . import creators
    cr = creators.get(creator_id) if creator_id else None
    if not cr:
        return None
    eligible = for_creator(creator_id)
    chosen = next((c for c in eligible if c["id"] == cr.get("campaign")), None)
    if chosen:
        return chosen
    return eligible[0] if len(eligible) == 1 else None


def needs_choice(creator_id) -> bool:
    from . import creators
    cr = creators.get(creator_id)
    eligible = for_creator(creator_id)
    return len(eligible) > 1 and not any(c["id"] == (cr or {}).get("campaign") for c in eligible)


def _handle(url: str) -> str:
    """The channel name in a channel or video link: twitch.tv/<name>, kick.com/<name>, youtube.com/@<name>."""
    p = urlparse(url if "://" in url else "https://" + url)
    parts = [x for x in (p.path or "").split("/") if x]
    if not parts:
        return ""
    first = parts[0].lstrip("@").lower()
    if first in ("channel", "c", "user") and len(parts) > 1:
        return parts[1].lower()
    return "" if first in ("watch", "videos", "shorts", "live") else first


def match_creator(meta: dict):
    """The creator whose channel a video comes from, judged by the channel link or channel id yt-dlp reports.
    Names alone are never enough: two channels can share a name."""
    from . import creators
    wanted = {h for h in (_handle(meta.get("channel_url") or ""), str(meta.get("uploader_id") or "").lstrip("@").lower()) if h}
    if not wanted:
        return None
    for cr in creators.all_creators():
        for url in (cr.get("channels") or {}).values():
            if _handle(url) in wanted:
                return cr["id"]
    return None


def attach(job, creator_id=None):
    """Give a job its campaign (when it has none yet) from its creator's default. Returns the campaign or None."""
    if job.get("campaign"):
        return get(job["campaign"])
    cid = creator_id or job.get("creator")
    c = default_for(cid) if cid else None
    if c:
        job["campaign"] = c["id"]
    elif cid and needs_choice(cid):
        job["campaign_needed"] = True
    return c


def brief_for(job) -> dict:
    """What the clip prompts need from a job's campaign: brief, lengths and rules. Empty when it has none."""
    c = get(job.get("campaign"))
    if not c:
        return {}
    return {"name": c["name"], "brief": c["brief"], "min_len": c["min_len"], "max_len": c["max_len"],
            "required_text": c["required_text"], "footage_rules": c["footage_rules"]}


# ---------------------------------------------------------------- the compliance check on a finished clip
def _facts(job, clip, c) -> dict:
    return {"duration": round(clip["end"] - clip["start"], 1), "hook": clip.get("hook", ""), "title": clip.get("title", ""),
            "description": clip.get("description", ""), "hashtags": clip.get("hashtags", []),
            "disclosure_on_screen": c["disclosure"], "required_tags_added": c["hashtags"],
            "required_text_added": c["required_text"], "source_title": job.get("title", ""),
            "source_channel": job.get("uploader", ""), "source_kind": job.get("kind", "video"),
            "made_at": time.strftime("%Y-%m-%d", time.localtime(clip.get("made_at") or time.time()))}


def rule_problems(job, clip, c) -> list:
    """The rules code can check for certain, without the AI."""
    out = []
    dur = clip["end"] - clip["start"]
    if dur < c["min_len"] - 1:
        out.append(f"Clip is {dur:.0f} s; the campaign wants at least {c['min_len']} s.")
    if dur > c["max_len"] + 1:
        out.append(f"Clip is {dur:.0f} s; the campaign allows at most {c['max_len']} s.")
    if not c["disclosure"].strip():
        out.append("No paid-partnership disclosure is set on the campaign, so the clip has none on screen.")
    if not accepting(c):
        out.append("The campaign is not accepting submissions right now (" + c["status"].replace("_", " ") + ").")
    return out


def check(job, clip) -> dict:
    """Pass or fail, with reasons. Code checks first; then the AI reads the brief against the clip's facts."""
    from . import brain, usage
    c = get(job.get("campaign") or clip.get("campaign"))
    if not c:
        return {"ok": True, "reasons": [], "t": time.time(), "by": "none"}
    reasons = rule_problems(job, clip, c)
    by = "rules"
    if c["brief"].strip() or c["footage_rules"].strip():
        try:
            usage.set_context(job.get("creator"), job.get("title") or "")
            ok, why = brain.check_compliance(c["brief"], c["footage_rules"], _facts(job, clip, c))
            by = "rules+ai"
            if not ok:
                reasons += why or ["The AI judged that this clip breaks the brief."]
        except Exception as e:  # noqa: BLE001
            reasons.append(f"The brief could not be checked by the AI ({str(e)[:120]}). Check it yourself.")
            by = "rules+ai-failed"
    return {"ok": not reasons, "reasons": reasons[:8], "t": time.time(), "by": by}


def postable(clip) -> str:
    """Why this campaign clip may not go into the post queue, or "" when it may."""
    if not clip.get("campaign"):
        return ""
    c = get(clip["campaign"])
    if not c:
        return "This clip's campaign was deleted. Choose another campaign for it first."
    if not accepting(c):
        return f"The campaign “{c['name']}” is not accepting submissions ({c['status'].replace('_', ' ')})."
    comp = clip.get("compliance")
    if comp is None:
        return "The campaign check has not finished yet for this clip."
    if not comp.get("ok") and not clip.get("compliance_override"):
        return "This clip failed the campaign check. Fix it, or press Override to post it anyway."
    return ""


def track(clip, state: str, **extra):
    """Move a campaign clip along: rendered, approved, posted, submitted, paid or rejected."""
    if state not in TRACK:
        raise ValueError("Unknown state")
    cr = clip.setdefault("cr", {"state": "rendered"})
    if cr.get("state") == "rejected" and state == "submitted":
        raise ValueError("A rejected post cannot be submitted again.")
    cr["state"] = state
    cr[state + "_at"] = time.time()
    for k, v in extra.items():
        if v is not None:
            cr[k] = v


def stats(jobs_list, posts) -> dict:
    """Clips, views and estimated earnings per campaign, from the jobs and the post queue."""
    rows = {}
    by_clip = {}
    for p in posts:
        if p.get("status") == "posted":
            by_clip.setdefault((p["job"], p["clip"]), []).append(p)
    for c in _mine():
        rows[c["id"]] = {"id": c["id"], "name": c["name"], "clips": 0, "passed": 0, "posted": 0, "submitted": 0,
                         "paid": 0, "rejected": 0, "views": 0, "payable": 0, "earnings": 0.0}
    for j in jobs_list:
        for k in j.get("clips", []):
            r = rows.get(k.get("campaign"))
            if not r or k.get("status") != "done":
                continue
            c = get(k["campaign"])
            r["clips"] += 1
            r["passed"] += bool((k.get("compliance") or {}).get("ok"))
            st = (k.get("cr") or {}).get("state", "rendered")
            for s in ("posted", "submitted", "paid", "rejected"):
                r[s] += st == s or (s == "posted" and st in ("submitted", "paid"))
            views = sum(p.get("views") or 0 for p in by_clip.get((j["id"], k["id"]), []))
            payable = int((k.get("cr") or {}).get("payable_views") or 0)
            r["views"] += views
            r["payable"] += payable
            pay = (payable or views) / 1000 * c["rate_per_1k"]
            if c["cap_per_clip"]:
                pay = min(pay, c["cap_per_clip"])
            r["earnings"] += pay
    for r in rows.values():
        r["earnings"] = round(r["earnings"] * (1 - config.CR_FEE), 2)
    return {"campaigns": list(rows.values()), "fee": config.CR_FEE}
