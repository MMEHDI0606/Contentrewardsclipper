"""The editing effects reach the clip: each playbook switches on its own effects (cut pauses, zoom in on the peak,
freeze and slow-motion replay), the clip gets the streamer's name tag, and a re-make keeps the choices made by hand.
Nothing is made or sent anywhere.

    python tests/test_effects_wiring.py"""
import json
import os
import sys
import tempfile

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-fx-")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import auth, creators, ctx, db, jobs, playbooks  # noqa: E402

db.init()
fails = []
FLAGS = ("trim_pauses", "punch", "replay")


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


class Always:
    """Lets experimental playbooks run every time, so the test does not depend on chance."""
    @staticmethod
    def random():
        return 0.0


def chosen(pid, category, length, has_chat=False):
    k = {"start": 0.0, "end": float(length), "category": category, "playbook": pid, "style": "bounce"}
    playbooks.choose(k, [], None, has_chat, rng=Always)
    return k


# ---- every playbook file still reads as JSON and is in the library
files = sorted(playbooks.DIR.glob("*.json"))
bad = []
for f in files:
    try:
        json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        bad.append(f"{f.name}: {e}")
check("every playbook file reads as JSON", files and not bad, bad)
check("every playbook file is in the library", len(playbooks.library()) == len(files), sorted(playbooks.library()))

# ---- each playbook switches on its own effects, and only those
want = {"plain": ("story", 30, False, ()),
        "long_story": ("story", 50, False, ("trim_pauses",)),
        "micro": ("funny", 12, False, ("trim_pauses",)),
        "chat_erupts": ("shock", 25, True, ("punch",)),
        "fail_replay": ("fail", 25, False, ("replay",))}
for pid, (cat, length, chat, on) in want.items():
    k = chosen(pid, cat, length, chat)
    got = {f: k.get(f) for f in FLAGS}
    check(f"{pid}: the playbook is kept", k["playbook"] == pid, k.get("playbook_reason"))
    check(f"{pid}: effects are {', '.join(on) or 'all off'}", got == {f: f in on for f in FLAGS}, got)
check("the effects are true or false, never missing", all(isinstance(chosen("plain", "story", 30)[f], bool) for f in FLAGS))

uid = auth.local_user().id
with ctx.as_user(uid):
    # ---- the clip carries the streamer's name, but only when it has a creator
    kai = creators.create({"name": "Kai", "channels": {"twitch": "https://twitch.tv/kaicenat"}, "channel": "https://twitch.tv/kaicenat"})
    job = jobs.create({"type": "url", "url": "https://twitch.tv/videos/1", "name": "x"}, {}, enqueue=False, creator=kai["id"])
    clip = jobs.prepare_clip(job, {"id": "c1", "start": 10.0, "end": 40.0, "score": 80, "category": "story", "playbook": "plain",
                                   "hook": "H", "title": "T", "description": "", "hashtags": [], "status": "done"})
    check("a creator's clip gets the streamer's name tag", clip.get("name_tag") == "Kai", clip.get("name_tag"))
    loose = jobs.create({"type": "url", "url": "https://twitch.tv/videos/2", "name": "y"}, {}, enqueue=False)
    k2 = jobs.prepare_clip(loose, {"id": "c2", "start": 10.0, "end": 40.0, "category": "story", "playbook": "plain"})
    check("a clip with no creator gets no name tag", "name_tag" not in k2)

    # ---- a re-make keeps the effects chosen by hand
    job["clips"] = [clip]
    jobs.request_render(job["id"], "c1", {"trim_pauses": True, "punch": True, "replay": False})
    check("a re-make stores the effects chosen", {f: clip[f] for f in FLAGS} == {"trim_pauses": True, "punch": True, "replay": False},
          {f: clip.get(f) for f in FLAGS})
    check("and queues the clip again", clip["status"] == "queued")
    jobs.request_render(job["id"], "c1", {"punch": 0})
    check("a re-make changes only the effects it names", (clip["trim_pauses"], clip["punch"]) == (True, False))
    jobs.request_render(job["id"], "c1", {"playbook": "fail_replay"})
    check("choosing a playbook by hand brings its effects", {f: clip[f] for f in FLAGS} == {"trim_pauses": False, "punch": False, "replay": True},
          {f: clip.get(f) for f in FLAGS})
    check("the effects and name tag reach the page", all(f in jobs.public(job)["clips"][0] for f in FLAGS + ("name_tag",)))

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
