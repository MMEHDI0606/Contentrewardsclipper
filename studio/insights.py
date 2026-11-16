"""What earns views: posted clips grouped by playbook, length, category, hour and so on; the health of each account;
and once a week, specific suggestions from the AI based on these numbers. Suggestions are only shown: the owner
accepts or rejects each one, and nothing changes the posting rules by itself."""
import json
import statistics
import threading
import time

from . import accounts, auth, config, ctx, jobs

MIN_FOR_REVIEW = 10           # posted clips with views before a weekly review is worth asking for


def _rows() -> list:
    """One row per posted clip: its features and its views."""
    from . import posting
    clips = {(j["id"], k["id"]): (j, k) for j in jobs.list_jobs() for k in j.get("clips", [])}
    out = []
    for p in posting._mine():
        if p.get("status") != "posted":
            continue
        j, k = clips.get((p["job"], p["clip"]), (None, None))
        if not k:
            continue
        lt = time.localtime(p["posted_at"])
        dur = k["end"] - k["start"]
        out.append({"views": p.get("views") or 0, "snaps": p.get("snaps") or {}, "account": p["account_name"],
                    "platform": p["platform"], "playbook": k.get("playbook") or "none", "variant": k.get("variant") or "",
                    "category": k.get("category") or "other", "style": k.get("style") or "",
                    "length": "under 20 s" if dur < 20 else "20-35 s" if dur < 35 else "35-50 s" if dur < 50 else "50 s+",
                    "hour": f"{lt.tm_hour:02d}:00", "day": time.strftime("%a", lt), "creator": j.get("creator") or "",
                    "campaign": k.get("campaign") or "", "score": k.get("score", 0),
                    "score band": "90+" if k.get("score", 0) >= 90 else "80-89" if k.get("score", 0) >= 80 else "70-79" if k.get("score", 0) >= 70 else "under 70",
                    "payable": int((k.get("cr") or {}).get("payable_views") or 0)})
    return out


def _group(rows, key) -> list:
    g = {}
    for r in rows:
        g.setdefault(r[key], []).append(r["views"])
    return sorted(({"group": k, "clips": len(v), "median": int(statistics.median(v)), "mean": int(sum(v) / len(v)),
                    "total": sum(v)} for k, v in g.items()), key=lambda x: -x["median"])


def _corr(xs, ys):
    if len(xs) < 5 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return round(num / den, 2) if den else None


def health() -> list:
    """Per account: posts a day this week, median views this week and last week, and whether reach dropped."""
    from . import posting
    now, out = time.time(), []
    for a in accounts.list_accounts():
        mine = [p for p in posting._mine() if p["account"] == a["id"] and p.get("status") == "posted"]
        week = [p.get("views") or 0 for p in mine if now - p["posted_at"] < 7 * 86400 and now - p["posted_at"] > 86400]
        prev = [p.get("views") or 0 for p in mine if 8 * 86400 <= now - p["posted_at"] < 15 * 86400]
        m1 = int(statistics.median(week)) if week else None
        m0 = int(statistics.median(prev)) if prev else None
        out.append({"account": a["name"], "platform": a["platform"],
                    "per_day": round(sum(1 for p in mine if now - p["posted_at"] < 7 * 86400) / 7, 1),
                    "median_week": m1, "median_before": m0,
                    "dropped": bool(m1 is not None and m0 and len(week) >= 3 and len(prev) >= 3 and m1 < 0.5 * m0)})
    return out


def summary() -> dict:
    rows = _rows()
    keys = ("playbook", "length", "category", "hour", "day", "platform", "account", "style", "score band", "campaign")
    best = sorted(rows, key=lambda r: -r["views"])
    return {"posted": len(rows), "groups": {k: _group(rows, k) for k in keys},
            "score_vs_views": _corr([r["score"] for r in rows], [r["views"] for r in rows]),
            "best": best[:5], "worst": best[-5:][::-1] if len(best) > 5 else [],
            "health": health(), "review": _read_review()}


# ---------------------------------------------------------------- the weekly review
def _file():
    return config.user_dir(ctx.uid()) / "review.json"


def _read_review() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_review(r):
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(r, indent=1), encoding="utf-8")
    ctx.bump()


def table_text(s=None) -> str:
    """The aggregate table the AI sees: groups and counts only, never names of people or accounts' contents."""
    s = s or summary()
    lines = [f"Posted clips with views: {s['posted']}. Correlation of AI score with views: {s['score_vs_views']}."]
    for k, groups in s["groups"].items():
        if k in ("account", "campaign"):
            continue                       # ids and handles stay out of the prompt
        lines.append(f"\nBy {k}:")
        lines += [f"  {g['group']}: {g['clips']} clips, median {g['median']}, mean {g['mean']}" for g in groups[:12]]
    return "\n".join(lines)


def run_review() -> dict:
    from . import brain
    s = summary()
    if s["posted"] < MIN_FOR_REVIEW:
        raise ValueError(f"A review needs at least {MIN_FOR_REVIEW} posted clips with views; there are {s['posted']}.")
    items = brain.weekly_review(table_text(s))
    r = {"t": time.time(), "items": [dict(i, id=n, decision="") for n, i in enumerate(items)],
         "score_vs_views": s["score_vs_views"]}
    _write_review(r)
    return r


def decide(item_id: int, decision: str) -> dict:
    if decision not in ("accepted", "rejected"):
        raise ValueError("Accept or reject.")
    r = _read_review()
    for i in r.get("items", []):
        if i["id"] == item_id:
            i["decision"] = decision
    _write_review(r)
    return r


def review_loop():
    """Once a week per user, when there is enough to go on."""
    while True:
        time.sleep(3600)
        for uid in auth.user_ids():
            try:
                with ctx.as_user(uid):
                    if time.time() - (_read_review().get("t") or 0) > 7 * 86400 and len(_rows()) >= MIN_FOR_REVIEW:
                        run_review()
            except Exception:  # noqa: BLE001
                pass


def start():
    threading.Thread(target=review_loop, daemon=True).start()
