"""Content Rewards, driven through a real browser (Playwright, using the Edge already on this computer).

It behaves like one person: one browser profile (data/cr-profile), one account, logged in by the owner in a visible
window. No password is ever read, stored or typed here; no stealth, no proxies, no CAPTCHA solving. If the site shows
a CAPTCHA, a block page or a layout this code does not recognise, it stops, saves a screenshot and says so.

What it does:
  - read a campaign page (public, or with more detail once joined): name, status, rates, payout cap, budget, platforms
    and the requirements text, into the Campaigns table;
  - list campaign links from Discover, and from the logged-in home page;
  - submit a posted clip's link to its campaign, only when every rule below holds, and only if the campaign is set to
    submit automatically (otherwise the owner submits it, and marks it here).
Joining campaigns, payouts and withdrawals are never automated.

Rules enforced in code (Creator Terms 7.11, 7.12): one campaign per post; a rejected post is never submitted again; a
clip that failed the campaign check is never submitted; a post older than 30 minutes cannot be submitted."""
import queue
import re
import threading
import time
from pathlib import Path

from . import alerts, campaigns, config, creators, ctx, jobs

PROFILE = config.DATA_DIR / "cr-profile"
SHOTS = config.DATA_DIR / "cr-shots"
WINDOW_MIN = 30                       # Creator Terms 7.12
_browser = threading.Lock()           # one browser on this profile at a time
_state = {"logged_in": None, "checked": 0.0, "problem": "", "login_open": False}
_todo: "queue.Queue" = queue.Queue()

# Words that mean "stop, a person must look". Kept in one place: the site will change.
STOP_WORDS = ("captcha", "verify you are human", "are you a robot", "access denied", "unusual traffic", "cloudflare")
CAMPAIGN_LINK = re.compile(r"^/c/campaigns/([0-9a-f-]{36})")


class Stop(RuntimeError):
    """The site showed something a person must deal with. The message says what; a screenshot is saved."""


def status() -> dict:
    return {**_state, "profile": PROFILE.exists(), "available": _available()}


def _available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _shot(page, what) -> str:
    SHOTS.mkdir(parents=True, exist_ok=True)
    f = SHOTS / f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', what.lower())[:40]}.png"
    try:
        page.screenshot(path=str(f), full_page=True)
    except Exception:  # noqa: BLE001
        return ""
    return f.name


def _guard(page, what):
    text = (page.inner_text("body") or "").lower()[:20000]
    if any(w in text for w in STOP_WORDS):
        shot = _shot(page, what + " blocked")
        _state["problem"] = f"Content Rewards showed a check for people ({what}). Open it yourself. Screenshot: {shot}"
        raise Stop(_state["problem"])
    return text


def _run(fn, headless=True):
    """Open the one browser on the saved profile, run fn(page), close it."""
    if not _available():
        raise RuntimeError("Playwright is not installed. Run: pip install -r requirements.txt")
    from playwright.sync_api import sync_playwright
    if not _browser.acquire(timeout=120):
        raise RuntimeError("The Content Rewards browser is busy (the login window may be open). Try again shortly.")
    try:
        with sync_playwright() as p:
            c = p.chromium.launch_persistent_context(str(PROFILE), channel="msedge", headless=headless,
                                                     viewport={"width": 1280, "height": 900})
            try:
                page = c.pages[0] if c.pages else c.new_page()
                return fn(page)
            finally:
                c.close()
    finally:
        _browser.release()


def _go(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)          # the site fills its pages in after loading


# ---------------------------------------------------------------- logging in (the owner, in a visible window)
def open_login():
    """A visible browser window on the login page. The owner signs in (2FA included) and closes the window."""
    if _state["login_open"]:
        return

    def run():
        _state["login_open"] = True

        def wait(page):
            _go(page, config.CR_URL + "/login")
            while True:
                try:
                    page.wait_for_timeout(1500)
                    if page.is_closed():
                        break
                except Exception:  # noqa: BLE001 - the window was closed
                    break
        try:
            _run(wait, headless=False)
        except Exception as e:  # noqa: BLE001
            _state["problem"] = str(e)[:300]
        finally:
            _state["login_open"] = False
            try:
                check_session()
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=run, daemon=True).start()


def check_session() -> bool:
    def look(page):
        _go(page, config.CR_URL + "/c/discover")
        _guard(page, "session check")
        signed_out = page.get_by_role("link", name=re.compile(r"^\s*sign in\s*$", re.I)).count() > 0
        return not signed_out
    ok = _run(look)
    _state.update(logged_in=ok, checked=time.time(), problem="" if ok else "Log in to Content Rewards (Campaigns, Log in).")
    return ok


# ---------------------------------------------------------------- reading campaigns
def _money(s):
    return float(s.replace(",", "").replace("$", "")) if s else 0.0


def parse_campaign(text: str, title: str = "") -> dict:
    """The fields of a campaign page, from its visible text. Anything not found is left out, never guessed."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = {}
    name = re.sub(r"\s*\|\s*Content Rewards\s*$", "", title or "").strip()
    name = re.sub(r"\s+by\s+[^|]+$", "", name).strip()
    if name:
        out["name"] = name[:120]
    plat = {}
    for i, l in enumerate(lines):
        key = {"tiktok": "tiktok", "instagram": "instagram", "youtube": "youtube"}.get(l.lower())
        if key and i + 2 < len(lines) and lines[i + 1].lower().startswith("per 1k"):
            plat[key] = {"rate": _money(re.search(r"\$[\d,.]+", lines[i + 2]).group(0)) if re.search(r"\$[\d,.]+", lines[i + 2]) else 0.0}
            seg = " ".join(lines[i: i + 9])
            m = re.search(r"Max payout\s*\$([\d,.]+)", seg)
            if m:
                plat[key]["cap"] = _money(m.group(1))
    if plat:
        out["platforms"] = list(plat)
        out["rate_per_1k"] = max(p["rate"] for p in plat.values())
        caps = [p["cap"] for p in plat.values() if p.get("cap")]
        if caps:
            out["cap_per_clip"] = max(caps)
    m = re.search(r"Budget\s*\$([\d,.]+)\s*/\s*\$([\d,.]+)", " ".join(lines))
    if m:
        spent, total = _money(m.group(1)), _money(m.group(2))
        out.update(budget_total=total, budget_left=max(0.0, total - spent))
    low = text.lower()
    if "accepting clips" in low:
        out["status"] = "active"
    elif any(w in low for w in ("campaign ended", "campaign has ended", "no longer accepting", "campaign is over")):
        out["status"] = "ended"
    elif "paused" in low:
        out["status"] = "paused"
    if out.get("status") == "active" and "budget_left" in out and out["budget_left"] <= 0:
        out["status"] = "out_of_budget"
    # the brief: the description under the title, and the requirements once joined
    brief = []
    if name and name in lines:
        i = lines.index(name)
        for l in lines[i + 1: i + 6]:
            if l.lower() in ("accepting clips", "join campaign") or l.lower().startswith("launched"):
                break
            brief.append(l)
    m = re.search(r"Content requirements\s*(.+?)(?:Top clippers|Reference materials|More from|$)", text, re.S | re.I)
    if m and "visible once you join" not in m.group(1).lower():
        brief.append("Requirements:\n" + m.group(1).strip())
    elif m:
        brief.append("(The requirements are only visible once you join this campaign.)")
    if brief:
        out["brief"] = "\n".join(brief)[:8000]
    return out


def read_campaign(url: str) -> dict:
    m = re.search(r"/c/campaigns/([0-9a-f-]{36})", url or "")
    if not m:
        raise ValueError("That is not a Content Rewards campaign link (…/c/campaigns/…).")

    def look(page):
        _go(page, f"{config.CR_URL}/c/campaigns/{m.group(1)}")
        text = _guard(page, "campaign page")
        got = parse_campaign(page.inner_text("body"), page.title())
        if "rate_per_1k" not in got and "budget_total" not in got:
            shot = _shot(page, "campaign layout changed")
            raise Stop(f"The campaign page did not look as expected, so nothing was read. Screenshot: {shot}")
        links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        got["source_links"] = sorted({l for l in links if re.search(r"(twitch\.tv|kick\.com|youtube\.com/(@|channel/))", l)})[:20]
        got["mentions"] = text[:4000]
        return got
    got = _run(look)
    got.update(url=f"{config.CR_URL}/c/campaigns/{m.group(1)}", cr_id=m.group(1), source="contentrewards")
    return got


def import_campaign(url: str, existing_id=None) -> dict:
    """Create (or refresh) a campaign from its Content Rewards page, and suggest creators to link it to."""
    got = read_campaign(url)
    suggest = suggestions(got)
    fields = {k: v for k, v in got.items() if k in campaigns.DEFAULTS}
    same = existing_id or next((c["id"] for c in campaigns.all_campaigns() if c.get("cr_id") == got["cr_id"]), None)
    c = campaigns.update(same, fields) if same else campaigns.create(fields)
    return {"campaign": c, "suggest": suggest}


def suggestions(got: dict) -> list:
    """Creators the campaign seems to be about: by a channel link on its page, or by the creator's name in its text.
    Only offered; the owner confirms every link, because a wrong link means clips submitted to the wrong campaign."""
    out = []
    text = (got.get("mentions") or "") + " " + (got.get("name") or "")
    for cr in creators.all_creators():
        urls = list((cr.get("channels") or {}).values())
        by_link = any(campaigns._handle(u) and campaigns._handle(u) in {campaigns._handle(l) for l in got.get("source_links", [])} for u in urls)
        by_name = bool(cr["name"]) and re.search(r"\b" + re.escape(cr["name"]) + r"\b", text, re.I)
        if by_link or by_name:
            out.append({"creator": cr["id"], "name": cr["name"], "why": "channel link on the campaign page" if by_link else "name mentioned"})
    known = {campaigns._handle(u) for cr in creators.all_creators() for u in (cr.get("channels") or {}).values()}
    for l in got.get("source_links", []):
        if campaigns._handle(l) and campaigns._handle(l) not in known:
            out.append({"channel": l, "why": "channel link on the campaign page, no workspace yet"})
    return out[:10]


def list_campaigns(where="discover") -> list:
    """Campaign links and names: from Discover (public), or from the logged-in home page (joined campaigns)."""
    path = "/c/discover" if where == "discover" else "/c"

    def look(page):
        _go(page, config.CR_URL + path)
        _guard(page, where)
        items = page.eval_on_selector_all("a[href]", "els => els.map(e => [e.getAttribute('href'), (e.innerText || '').trim()])")
        seen, out = set(), []
        for href, label in items:
            m = CAMPAIGN_LINK.match(href or "")
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                bits = [b for b in label.splitlines() if b.strip()]
                out.append({"url": f"{config.CR_URL}/c/campaigns/{m.group(1)}", "label": " · ".join(bits[:4])[:160]})
        if not out:
            raise Stop(f"No campaigns were found on that page; it may have changed. Screenshot: {_shot(page, where + ' empty')}")
        return out
    return _run(look)


# ---------------------------------------------------------------- submitting a posted clip
def submit_problem(job, clip) -> str:
    """Why this clip may not be submitted, or "" when it may (the rules from the Creator Terms)."""
    c = campaigns.get(clip.get("campaign"))
    cr = clip.get("cr") or {}
    if not c:
        return "The clip has no campaign."
    if not c.get("url") or "/c/campaigns/" not in c["url"]:
        return "The campaign has no Content Rewards link."
    if not campaigns.accepting(c):
        return "The campaign is not accepting submissions."
    if cr.get("state") == "rejected":
        return "A rejected post is never submitted again."
    if cr.get("state") in ("submitted", "paid"):
        return "Already submitted."
    if not (clip.get("compliance") or {}).get("ok") and not clip.get("compliance_override"):
        return "The clip failed the campaign check."
    if not cr.get("post_url"):
        return "The clip has not been posted yet."
    if any(k is not clip and (k.get("cr") or {}).get("post_url") == cr["post_url"] and (k.get("cr") or {}).get("state") in ("submitted", "paid")
           for j in jobs.list_jobs() for k in j.get("clips", [])):
        return "That post was already submitted to a campaign (one campaign per post)."
    posted = cr.get("posted_at") or 0
    if posted and time.time() - posted > WINDOW_MIN * 60:
        return f"More than {WINDOW_MIN} minutes have passed since posting, so it can no longer be submitted."
    return ""


# The submission form is only visible to a joined, logged-in creator. These are the words the code looks for; it
# never fills anything else. If they are not found, it stops and asks you to submit by hand.
SUBMIT_BUTTON = re.compile(r"^\s*(submit( a)? (clip|post|video)|submit)\s*$", re.I)
URL_FIELD = re.compile(r"(post|video|clip)?\s*(link|url)", re.I)
CONFIRM_BUTTON = re.compile(r"^\s*(submit|confirm|send)\s*$", re.I)


def submit(job, clip) -> str:
    why = submit_problem(job, clip)
    if why:
        raise ValueError(why)
    c = campaigns.get(clip["campaign"])
    post_url = clip["cr"]["post_url"]

    def run(page):
        _go(page, c["url"])
        _guard(page, "submit")
        btn = page.get_by_role("button", name=SUBMIT_BUTTON)
        if btn.count() == 0:
            raise Stop(f"No submit button was found (not logged in, not joined, or the page changed). "
                       f"Screenshot: {_shot(page, 'no submit button')}")
        btn.first.click()
        page.wait_for_timeout(1500)
        field = page.get_by_role("textbox", name=URL_FIELD)
        if field.count() != 1:
            raise Stop(f"The submit form did not look as expected, so nothing was sent. Screenshot: {_shot(page, 'submit form')}")
        field.first.fill(post_url)
        ok = page.get_by_role("button", name=CONFIRM_BUTTON)
        if ok.count() == 0:
            raise Stop(f"No confirm button on the submit form. Screenshot: {_shot(page, 'no confirm')}")
        ok.last.click()
        page.wait_for_timeout(4000)
        text = _guard(page, "after submit")
        shot = _shot(page, "submitted")
        if not any(w in text for w in ("submitted", "received", "under review", "pending review")):
            raise Stop(f"The site did not confirm the submission. Check it yourself. Screenshot: {shot}")
        return shot
    shot = _run(run)
    campaigns.track(clip, "submitted", screenshot=shot)
    jobs.save(job)
    creators.log(job.get("creator"), f"Submitted to {c['name']}: {post_url}")
    return shot


def queue_submit(job_id, clip_id):
    _todo.put((ctx.uid(), job_id, clip_id, time.time()))


def _worker():
    while True:
        uid, jid, cid, t = _todo.get()
        with ctx.as_user(uid):
            job = jobs.get(jid)
            clip = next((k for k in (job or {}).get("clips", []) if k["id"] == cid), None)
            if not clip:
                continue
            try:
                submit(job, clip)
            except Exception as e:  # noqa: BLE001
                clip.setdefault("cr", {})["error"] = str(e)[:300]
                jobs.save(job)
                left = WINDOW_MIN - (time.time() - (clip["cr"].get("posted_at") or t)) / 60
                alerts.notify(f"could not submit a clip automatically ({e}). Submit it by hand in the next {max(0, int(left))} minutes.",
                              kind="post")


def _window_watch():
    """Warn when a posted campaign clip has not been submitted and its 30 minutes are nearly up."""
    from . import auth
    warned = set()
    while True:
        time.sleep(60)
        for uid in auth.user_ids():
            try:
                with ctx.as_user(uid):
                    for j in jobs.list_jobs():
                        for k in j.get("clips", []):
                            cr = k.get("cr") or {}
                            if cr.get("state") == "posted" and cr.get("posted_at"):
                                age = (time.time() - cr["posted_at"]) / 60
                                key = (j["id"], k["id"])
                                if 20 <= age < WINDOW_MIN and key not in warned:
                                    warned.add(key)
                                    alerts.notify(f"submit soon: {int(WINDOW_MIN - age)} minutes left to submit "
                                                  f"“{k.get('title') or k.get('hook')}”.", kind="post")
            except Exception:  # noqa: BLE001
                pass


def start():
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_window_watch, daemon=True).start()
