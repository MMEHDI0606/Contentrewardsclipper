"""The Claude Code session as the AI ("the brain").

Instead of calling an AI service over the internet, a request is put in a queue here and the caller waits. A
Claude Code session, running in its own window on this computer, takes requests through a small MCP server
(brain/server.ts) that talks to /api/brain/* on this server, answers each one, and the waiting caller gets the
answer. Nothing here holds a key: only the official `claude` program talks to Anthropic.

    PEAKCUT_BRAIN_LEASE        seconds the brain has to answer a request it took before it is offered again (180)
    PEAKCUT_BRAIN_VIDEO_WAIT   seconds a video waits for an answer before giving up on that part (600)
    PEAKCUT_BRAIN_LIVE_WAIT    seconds a live check waits before it is skipped (120)
    PEAKCUT_BRAIN_MAX_WAIT     longest a single "next request" call may wait for work (300)"""
import collections
import hmac
import itertools
import os
import secrets
import threading
import time

from . import config

LEASE = int(config.env("PEAKCUT_BRAIN_LEASE") or 180)
VIDEO_WAIT = int(config.env("PEAKCUT_BRAIN_VIDEO_WAIT") or 600)
LIVE_WAIT = int(config.env("PEAKCUT_BRAIN_LIVE_WAIT") or 120)
MAX_WAIT = int(config.env("PEAKCUT_BRAIN_MAX_WAIT") or 300)
TRIES = 2              # a broken answer may be corrected once
BACKLOG = int(config.env("PEAKCUT_BRAIN_BACKLOG") or 4)   # waiting requests beyond this: the oldest live checks are skipped
BACKED_UP = "Skipped: the Claude Code session has a backlog, so the oldest live checks give way."
SEEN_FOR = 90          # the brain counts as connected this long after it last asked for work

# These words must stay out of every error raised here: brain.ask_ai retries three times on "HTTP 429" or
# "HTTP 5", and ai.explain reads "timeout", "connect" and similar as an internet problem.
GAVE_UP = "The Claude Code session gave no answer within {mins} minutes."
NOT_RUNNING = "The Claude Code session (the brain) is not running."


class Unanswered(RuntimeError):
    pass


_lock = threading.Lock()
_reqs: dict = {}
_ids = itertools.count(1)
_seen = {"poll": 0.0, "answer": 0.0, "streams": 0, "served": 0, "dropped_live": 0}
_served_at: collections.deque = collections.deque(maxlen=5000)     # when each request was handed out, for "per hour"
_mine = threading.local()
_token = {"value": ""}


def tag(job_id):
    """Which video or recording the AI calls on this thread are for, so its page can say it is waiting."""
    _mine.job = job_id


# ---------------------------------------------------------------- the shared secret with the MCP server
def token_file():
    return config.DATA_DIR / "brain.token"


def token() -> str:
    """A new random secret each time the server starts, written where the MCP server reads it."""
    if not _token["value"]:
        value = secrets.token_urlsafe(32)
        f = token_file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(value, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(f)
        _token["value"] = value
    return _token["value"]


def token_ok(given: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), token().encode())


# ---------------------------------------------------------------- the waiting side (job and live threads)
def _kind(purpose: str) -> str:
    p = (purpose or "").lower()
    return "live" if p.startswith("live") else "test" if p.startswith("settings") else "video"


def connected() -> bool:
    return _seen["streams"] > 0 or time.time() - _seen["poll"] < SEEN_FOR


def ask(prompt: str, purpose: str, schema=None):
    """Queue one request and wait for the brain's answer. Returns (parsed JSON, length of the answer text)."""
    kind = _kind(purpose)
    if kind != "video" and not connected():
        raise Unanswered(NOT_RUNNING + (" This live check was skipped." if kind == "live" else ""))
    wait = {"live": LIVE_WAIT, "test": 90}.get(kind, VIDEO_WAIT)
    r = {"id": f"r{next(_ids)}{secrets.token_hex(3)}", "purpose": purpose or "AI call", "prompt": prompt,
         "created_at": time.time(), "lease_until": 0.0, "status": "queued", "tries": 0, "kind": kind,
         "array": (schema or {}).get("type") == "ARRAY", "job": getattr(_mine, "job", None),
         "answer": None, "error": "", "done": threading.Event()}
    with _lock:
        _reqs[r["id"]] = r
        waiting = sorted((x for x in _reqs.values() if x["status"] == "queued"), key=lambda x: x["created_at"])
        for old in [x for x in waiting if x["kind"] == "live"][:max(0, len(waiting) - BACKLOG)]:
            old.update(status="answered", error=BACKED_UP)     # never let a backlog hold up everything
            old["done"].set()
    try:
        r["done"].wait(wait)
    finally:
        with _lock:
            _reqs.pop(r["id"], None)
    if r["answer"] is None:
        if kind == "live":
            _seen["dropped_live"] += 1
        raise Unanswered(r["error"] or GAVE_UP.format(mins=max(1, round(wait / 60))))
    return r["answer"]


# ---------------------------------------------------------------- the brain's side (through /api/brain/*)
def _free(r, now) -> bool:
    return r["status"] == "queued" or (r["status"] == "leased" and r["lease_until"] < now)


def lease():
    """The oldest request nobody is working on (or whose lease ran out), now leased to the brain. None if idle."""
    now = time.time()
    with _lock:
        _seen["poll"] = now
        free = [r for r in _reqs.values() if _free(r, now)]
        if not free:
            return None
        r = min(free, key=lambda x: x["created_at"])
        r.update(status="leased", lease_until=now + LEASE)
        _seen["served"] += 1
        _served_at.append(now)
        return {"id": r["id"], "purpose": r["purpose"], "prompt": r["prompt"]}


def answer(rid: str, text: str):
    """The brain's answer to one request. Returns (accepted, message for the brain)."""
    from . import ai
    with _lock:
        r = _reqs.get(rid)
        if not r or r["status"] == "answered":
            return False, "That request is no longer waiting (answered already, or the app gave up on it). Call next_request."
    try:
        out = ai._parse(text)
        if r["array"] and not isinstance(out, list):
            raise ValueError('the answer must be {"items": [...]} with a JSON array inside')
    except Exception as e:  # noqa: BLE001
        with _lock:
            r["tries"] += 1
            if r["tries"] >= TRIES:
                r.update(status="answered", error="The Claude Code session did not answer with valid JSON.")
                r["done"].set()
                return False, "Still not valid JSON, so this request was dropped. Call next_request."
        return False, f"Not valid JSON ({str(e)[:160]}). Submit the corrected JSON for the same id."
    with _lock:
        r.update(status="answered", answer=(out, len(text)))
        _seen["answer"] = time.time()
        r["done"].set()
    return True, "Received."


def pending() -> int:
    now = time.time()
    with _lock:
        return sum(1 for r in _reqs.values() if _free(r, now))


def stream(opened: bool):
    """The MCP server holds /api/brain/events open while its Claude Code session is running."""
    with _lock:
        _seen["streams"] = max(0, _seen["streams"] + (1 if opened else -1))


def waiting_for(job_id) -> bool:
    with _lock:
        return any(r["job"] == job_id for r in _reqs.values())


def status() -> dict:
    now = time.time()
    with _lock:
        reqs = list(_reqs.values())
    ago = lambda t: round(now - t) if t else None                        # noqa: E731
    return {"connected": connected(), "session_open": _seen["streams"] > 0,
            "last_poll_s": ago(_seen["poll"]), "last_answer_s": ago(_seen["answer"]),
            "pending": sum(1 for r in reqs if _free(r, now)), "working": sum(1 for r in reqs if not _free(r, now)),
            "oldest_s": ago(min((r["created_at"] for r in reqs), default=0)),
            "served": _seen["served"], "live_skipped": _seen["dropped_live"],
            "per_hour": sum(1 for t in list(_served_at) if now - t < 3600)}
