"""The Claude Code session as the AI: requests wait in a queue, a fake brain takes and answers them. Covers a normal
answer, a broken answer corrected once, a lease running out, giving up, and the brain's web door (secret, this
computer only). Nothing leaves this machine.

    python tests/test_bridge.py [address of a running test server]"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_DATA = os.environ.get("PEAKCUT_DATA", "")
BASE = sys.argv[1] if len(sys.argv) > 1 else ""
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-bridge-")
os.environ["DATABASE_URL"] = ""
os.environ.update(PEAKCUT_AI_PROVIDER="claude_session", PEAKCUT_AI_MODELS="sonnet", PEAKCUT_AI_KEY="",
                  PEAKCUT_BRAIN_LEASE="1", PEAKCUT_BRAIN_VIDEO_WAIT="2", PEAKCUT_BRAIN_LIVE_WAIT="2")
sys.path.insert(0, ".")
from studio import ai, auth, brain, bridge, ctx, db  # noqa: E402

db.init()
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


CLIP = {"start": 1, "end": 20, "score": 80, "hook": "WOW", "title": "t", "description": "d", "hashtags": ["a"], "reason": "r"}


def fake_brain(answers, seen, stop):
    """Takes each request and answers with the next text in `answers` (None: take it and say nothing)."""
    while not stop.is_set():
        r = bridge.lease()
        if not r:
            time.sleep(0.05)
            continue
        seen.append(r)
        while answers:
            text = answers.pop(0)
            if text is None:
                break
            ok, msg = bridge.answer(r["id"], text)
            seen.append((ok, msg))
            if ok or "dropped" in msg:
                break


def run_brain(answers):
    seen, stop = [], threading.Event()
    t = threading.Thread(target=fake_brain, args=(answers, seen, stop), daemon=True)
    t.start()
    return seen, stop


owner = auth.local_user()
with ctx.as_user(owner.id):
    check("the Claude Code session needs no key and no address", ai.ready(), ai.public())
    check("its models are a fixed list, asked of no service", ai.list_models("claude_session") == ai.SESSION_MODELS)

    # 1. a normal answer
    seen, stop = run_brain([json.dumps({"items": [CLIP]})])
    out, model, tokens = brain.ask_ai("find clips please", purpose="Video: finding clips")
    stop.set()
    check("a normal answer comes back parsed", out == [CLIP], out)
    check("the brain sees the purpose and the JSON instruction", seen and seen[0]["purpose"] == "Video: finding clips"
          and '"items"' in seen[0]["prompt"], seen[:1])
    check("tokens are estimated from the text (about 4 characters each)", tokens > 0, tokens)

    # 2. broken JSON, then corrected
    seen, stop = run_brain(["sure! here are the clips", json.dumps({"items": [CLIP]})])
    out, _m, _t = brain.ask_ai("find clips please", purpose="Video: finding clips")
    stop.set()
    first = [x for x in seen if isinstance(x, tuple)]
    check("a broken answer is refused with a reason the brain can act on", first and not first[0][0] and "Not valid JSON" in first[0][1], first)
    check("the corrected answer is taken", out == [CLIP], out)

    # 2b. an object where a list was asked for
    seen, stop = run_brain([json.dumps({"start": 1}), json.dumps([CLIP])])
    out, _m, _t = brain.ask_ai("find clips please", purpose="Video: finding clips")
    stop.set()
    check("an answer of the wrong shape is refused, then a plain list is taken", out == [CLIP], seen)

    # 3. a lease runs out and the request is offered again
    seen, stop = run_brain([None])
    box = {}
    w = threading.Thread(target=lambda: box.update(r=bridge.ask("p", "Video: finding clips", {"type": "ARRAY"})), daemon=True)
    w.start()
    time.sleep(0.3)
    stop.set()
    took = seen[0]["id"] if seen else None
    time.sleep(1.2)
    again = bridge.lease()
    check("a request whose lease ran out is offered again", again and again["id"] == took, (took, again))
    if again:
        bridge.answer(again["id"], json.dumps([CLIP]))
    w.join(3)
    check("and its answer still reaches the caller", box.get("r", (None,))[0] == [CLIP], box)

    # 4. nobody answers: a plain error, and no retries
    t0 = time.time()
    try:
        brain.ask_ai("find clips please", purpose="Video: finding clips")
        check("giving up raises an error", False)
    except RuntimeError as e:
        took = time.time() - t0
        text = str(e)
        check("giving up raises an error", True)
        check("the error never looks like an HTTP 5xx or 429 (which would be retried)", "HTTP 5" not in text and "HTTP 429" not in text, text)
        check("it is given up once, not retried three times", took < 4, f"{took:.1f}s")
        check("the dashboard is told the brain is not answering", "Claude Code session" in ai.problem["text"], ai.problem)
    check("a request that was given up on is gone from the queue", bridge.pending() == 0, bridge.status())

    # 5. a live check with no brain running is skipped at once
    bridge._seen.update(poll=0.0, streams=0)
    t0 = time.time()
    try:
        bridge.ask("p", "Live: words judge", {"type": "ARRAY"})
        check("a live check without a brain is skipped", False)
    except bridge.Unanswered as e:
        check("a live check without a brain is skipped at once", time.time() - t0 < 0.5 and "skipped" in str(e), str(e))

    # 6. broken twice: dropped, and the caller is told
    seen, stop = run_brain(["nope", "still nope"])
    try:
        bridge.ask("p", "Video: finding clips", {"type": "ARRAY"})
        check("two broken answers end the request", False)
    except bridge.Unanswered as e:
        check("two broken answers end the request with a plain message", "valid JSON" in str(e), str(e))
    stop.set()

    # 7. which video is waiting
    def a_job_thread():
        bridge.tag("job123")             # set on the thread that does the work, as jobs.py and live.py do
        try:
            bridge.ask("p", "Video: finding clips")
        except bridge.Unanswered:
            pass
    w = threading.Thread(target=a_job_thread, daemon=True)
    w.start()
    time.sleep(0.2)
    check("the video a request belongs to shows as waiting", bridge.waiting_for("job123") and not bridge.waiting_for("other"))
    w.join(3)

# 8. the brain's web door on the running test server
if BASE and SERVER_DATA:
    tok_file = Path(SERVER_DATA) / "brain.token"
    tok = tok_file.read_text(encoding="utf-8").strip() if tok_file.exists() else ""
    check("the server writes its brain secret into the data folder", len(tok) >= 32)

    def get(path, headers=None):
        req = urllib.request.Request(BASE + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}

    check("no secret: refused", get("/api/brain/next?wait=0")[0] == 401)
    check("wrong secret: refused", get("/api/brain/next?wait=0", {"X-PeakCut-Brain": "x" * 43})[0] == 401)
    check("through a proxy: refused", get("/api/brain/next?wait=0", {"X-PeakCut-Brain": tok, "X-Forwarded-For": "1.2.3.4"})[0] == 404)
    st, body = get("/api/brain/next?wait=0", {"X-PeakCut-Brain": tok})
    check("with the secret and nothing to do: idle", st == 200 and body.get("idle") is True, (st, body))
    t0 = time.time()
    st, body = get("/api/brain/next?wait=1.5", {"X-PeakCut-Brain": tok})
    check("asking for work waits a while before saying idle", st == 200 and time.time() - t0 >= 1.2, time.time() - t0)
    req = urllib.request.Request(BASE + "/api/brain/answer", data=json.dumps({"id": "nope", "answer": "[]"}).encode(),
                                 headers={"X-PeakCut-Brain": tok, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read())
    check("an answer to an unknown request is refused politely", body.get("ok") is False and "next_request" in body.get("message", ""), body)

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
