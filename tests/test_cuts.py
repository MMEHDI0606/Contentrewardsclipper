"""A version of each clip per platform: the length settings, where a shorter or longer version starts (sentence
starts, never inside a word, the end kept), the versions made after the main clip (with a stand-in for ffmpeg),
which file is posted to each platform, and deleting a clip with its versions. Nothing leaves this machine.

    python tests/test_cuts.py"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-cuts-")
os.environ["DATABASE_URL"] = ""
os.environ.update(PEAKCUT_AI_KEY="", PEAKCUT_AI_MODELS="")
for k in ("PEAKCUT_CUT_TIKTOK", "PEAKCUT_CUT_INSTAGRAM", "PEAKCUT_CUT_YOUTUBE"):
    os.environ.pop(k, None)
sys.path.insert(0, ".")
from studio import account, accounts, auth, config, ctx, db, jobs, posting  # noqa: E402

db.init()
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


def talk(sentences, t=0.0, gap=0.8, mark="."):
    """A transcript: each sentence is a number of words, 0.4 s each with 0.1 s between, and a pause after it."""
    out = []
    for n in sentences:
        for i in range(n):
            out.append({"w": f"w{len(out)}" + (mark if i == n - 1 else ""), "s": round(t, 2), "e": round(t + 0.4, 2)})
            t += 0.5
        t += gap - 0.1
    return out


def sentence_starts(words):
    return {w["s"] for i, w in enumerate(words) if i and words[i - 1]["w"][-1:] in ".?!"}


def mid_word(t, words):
    return any(w["s"] + 0.001 < t < w["e"] - 0.001 for w in words)


def starts_at_sentence(t, words):
    """The start is a sentence's first word, give or take the short breath kept before it."""
    return any(0 <= s - t <= 0.16 for s in sentence_starts(words))


# ---------------------------------------------------------------- settings
check("default lengths: TikTok and Instagram 15-35, YouTube 30-60",
      config.PLATFORM_CUTS == {"tiktok": (15.0, 35.0), "instagram": (15.0, 35.0), "youtube": (30.0, 60.0)}, config.PLATFORM_CUTS)
check("the platforms are the ones accounts are connected for", set(config.PLATFORM_CUTS) == set(accounts.PLATFORM_NAMES))
os.environ["PEAKCUT_CUT_TIKTOK"] = " 20 - 40 "
check("a length range is read as shortest-longest", config._cut_range("PEAKCUT_CUT_TIKTOK", "15-35") == (20.0, 40.0))
for bad in ("", "off", "40-20", "abc"):
    os.environ["PEAKCUT_CUT_TIKTOK"] = bad
    check(f"'{bad}' means no separate version", config._cut_range("PEAKCUT_CUT_TIKTOK", "15-35") is None)
os.environ.pop("PEAKCUT_CUT_TIKTOK")

# ---------------------------------------------------------------- where a version starts and ends
words = talk([12, 9, 15, 7, 11, 10, 13, 8, 12, 9, 14, 10])          # about 75 seconds of talking
ss = sorted(sentence_starts(words))
end = next(w["e"] for w in words if w["s"] > 60 and w["w"].endswith("."))   # a clip that ends on a sentence end
clip = {"start": ss[0], "end": end}

got = jobs.platform_cut(clip, words, 15, 35)
check("too long: a shorter version is made", got is not None)
check("shorter: the end stays on the peak", got and got[1] == round(end, 2), got)
check("shorter: inside the range", got and 15 <= got[1] - got[0] <= 35.05, got)
check("shorter: starts later, at a sentence start", got and got[0] > clip["start"] and starts_at_sentence(got[0], words), got)
check("shorter: keeps as much setup as fits", got and not any(end - 35 + 0.15 <= s < got[0] + 0.15 for s in ss), got)
check("shorter: never inside a word", got and not mid_word(got[0], words), got)

short = {"start": ss[-3], "end": end}
got = jobs.platform_cut(short, words, 30, 60)
check("too short: a longer version is made", got is not None and short["end"] - short["start"] < 30)
check("longer: the end stays on the peak", got and got[1] == round(end, 2), got)
check("longer: inside the range", got and 30 <= got[1] - got[0] <= 60.05, got)
check("longer: starts earlier, at a sentence start", got and got[0] < short["start"] and starts_at_sentence(got[0], words), got)
check("longer: adds only as much setup as needed", got and not any(got[0] + 0.2 < s <= end - 30 for s in ss), got)
check("longer: never inside a word", got and not mid_word(got[0], words), got)

fits = {"start": ss[3], "end": ss[3] + 25}
check("already the right length: no version (the main clip is used)", jobs.platform_cut(fits, words, 15, 35) is None)

flat = talk([12, 9, 15, 7, 11, 10, 13, 8, 12, 9, 14, 10], gap=0.8, mark="")      # no punctuation, like YouTube's
fss = sorted(sentence_starts([dict(w, w=w["w"] + ("." if i + 1 < len(flat) and flat[i + 1]["s"] - w["e"] > 0.5 else ""))
                              for i, w in enumerate(flat)]))
got = jobs.platform_cut({"start": fss[0], "end": end}, flat, 15, 35)
check("no punctuation: starts after a pause", got and any(0 <= s - got[0] <= 0.16 for s in fss), got)

one = talk([150], gap=0.1, mark="")                       # one long unbroken stretch: no sentences, no pauses
got = jobs.platform_cut({"start": 0.0, "end": 70.0}, one, 15, 35)
check("no sentence in reach: still a word start and in range", got and not mid_word(got[0], one) and 15 <= got[1] - got[0] <= 35.05, got)

quiet = [w for w in words if w["s"] < 5 or w["s"] > 55]   # nothing said for most of it
got = jobs.platform_cut({"start": 0.0, "end": end}, quiet, 15, 35)
check("long silence: cut down to the longest allowed, not inside a word",
      got and got[1] - got[0] <= 35.05 and not mid_word(got[0], quiet), got)

early = {"start": 0.0, "end": round(ss[1] - 0.8, 2)}          # the very start of the video: nothing before it
got = jobs.platform_cut(early, words, 30, 60)
check("start of the video: never before 0, runs on to a later sentence end instead",
      got and got[0] >= 0 and got[1] > early["end"] and 30 <= got[1] - got[0] <= 60 and got[1] in {w["e"] for w in words}, got)

piece = {"start": 40.0, "end": end, "src_offset": 38.0, "src_end": end + 5}
got = jobs.platform_cut(piece, words, 30, 60)
check("a clip from a fetched piece never starts before the piece", got and got[0] >= 38.0, got)
check("nothing to add or remove: no version", jobs.platform_cut(dict(early, end=early["start"]), [], 30, 60) is None)

# ---------------------------------------------------------------- the versions made after the main clip
uid = auth.local_user().id
made = []


def fake_render(src, out_dir, name, clip, words, w, h):
    if name.endswith("_youtube") and fail_youtube:
        raise RuntimeError("encoder stopped")
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("mp4", "jpg"):
        (out_dir / f"{name}.{ext}").write_text("x")
    made.append((name, round(clip["start"], 2), round(clip["end"], 2), clip.get("punch")))
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "layout": "blur"}


jobs.render.render_clip = fake_render
with ctx.as_user(uid):
    job = jobs.create({"type": "file", "path": "x.mp4", "name": "x"}, {}, enqueue=False)
    job.update(source_path="x.mp4", width=1920, height=1080, duration=200.0, status="done")
    c = {"id": "clip01", "start": round(ss[0], 2), "end": round(end, 2), "hook": "", "title": "t", "description": "",
         "hashtags": [], "score": 80, "version": 1, "punch": True}
    c.update(fixed_words=[w for w in words if c["start"] - 1 <= w["s"] <= c["end"] + 1], fixed_for=[c["start"], c["end"], "en2"])
    job["clips"].append(c)
    fail_youtube = True
    jobs.render_one(job, c, words)
    clips_dir = Path(job["dir"]) / "clips"
    check("a clip too long for TikTok and Instagram gets their versions", set(c.get("cuts", {})) == {"tiktok", "instagram"}, c.get("cuts"))
    check("a failed version does not fail the clip", c["status"] == "done" and "youtube" not in c["cuts"], c.get("error"))
    tk = c["cuts"].get("tiktok", {})
    check("a version records its file, cover, start and end",
          tk.get("file") == "clip01_tiktok.mp4" and tk.get("cover") == "clip01_tiktok.jpg" and tk.get("end") == c["end"]
          and 15 <= tk["end"] - tk["start"] <= 35.05, tk)
    check("the clip's other settings reach the version untouched", all(m[3] for m in made), made)
    check("the browser sees the versions", jobs.public(job)["clips"][0].get("cuts") == c["cuts"])

    c.update(start=round(end - 25, 2))                    # made shorter by hand: fits TikTok and Instagram now
    c["fixed_words"] = [w for w in words if c["start"] - 1 <= w["s"] <= c["end"] + 1]
    c["fixed_for"] = [c["start"], c["end"], "en2"]
    fail_youtube = False
    jobs.render_one(job, c, words)
    check("re-made: versions no longer needed are dropped", set(c["cuts"]) == {"youtube"}, c["cuts"])
    check("re-made: their files are deleted", not (clips_dir / "clip01_tiktok.mp4").exists() and not (clips_dir / "clip01_instagram.jpg").exists())
    yt = c["cuts"].get("youtube", {})
    check("a clip too short for YouTube gets a longer version", yt and 30 <= yt["end"] - yt["start"] <= 60.05, yt)

    # ------------------------------------------------------------ which file is posted
    sent = {}
    accounts.get = lambda aid: {"id": aid, "name": aid, "platform": aid}
    accounts.youtube_upload = lambda acc, path, *a: (sent.__setitem__("youtube", path.name), ("r1", "u1"))[1]
    accounts.tiktok_upload = lambda acc, path, *a: (sent.__setitem__("tiktok", path.name), ("r2", "u2"))[1]
    (clips_dir / "clip01.mp4").write_text("x")
    for plat in ("youtube", "tiktok"):
        p = {"id": plat, "user": uid, "job": job["id"], "clip": "clip01", "creator": None, "account": plat, "account_name": plat,
             "platform": plat, "status": "posting", "due_at": 0, "title": "t", "tries": 0, "error": None}
        posting._posts.append(p)
        posting._post(p)
    check("YouTube gets its own version", sent.get("youtube") == "clip01_youtube.mp4", sent)
    check("a platform without its own version gets the main clip", sent.get("tiktok") == "clip01.mp4", sent)
    (clips_dir / "clip01_youtube.mp4").unlink()
    sent.clear()
    p = dict(p, id="again", account="youtube", platform="youtube", status="posting")
    posting._post(p)
    check("a version whose file is missing: the main clip is posted", sent.get("youtube") == "clip01.mp4", sent)

    # ------------------------------------------------------------ video files deleted a day after posting
    (clips_dir / "clip01_youtube.mp4").write_text("x")
    (clips_dir / "clip01_youtube.jpg").write_text("x")
    mine = [q for q in posting._posts if q["clip"] == "clip01"]
    posting._tidy()
    check("posted just now: the files stay", (clips_dir / "clip01.mp4").exists() and not jobs.get(job["id"])["clips"][0].get("files_deleted"))
    for q in mine:
        q["posted_at"] = 0
    waiting = dict(mine[0], id="later", status="scheduled")
    posting._posts.append(waiting)
    posting._tidy()
    check("a post of it still waiting: the files stay", (clips_dir / "clip01.mp4").exists())
    waiting["status"] = "cancelled"
    posting._tidy()
    c = jobs.get(job["id"])["clips"][0]
    check("a day after its last post: the clip's videos are deleted", not any(clips_dir.glob("clip01*.mp4")),
          [f.name for f in clips_dir.iterdir()])
    check("its cover pictures and record stay", (clips_dir / "clip01_youtube.jpg").exists() and c.get("files_deleted") and c["status"] == "done")

    # ------------------------------------------------------------ deleting
    (clips_dir / "clip01_youtube.mp4").write_text("x")
    account.delete_clip(job["id"], "clip01")
    check("deleting a clip deletes its versions too", not any(clips_dir.glob("clip01*.mp4")) and not any(clips_dir.glob("clip01*.jpg")),
          [f.name for f in clips_dir.iterdir()])

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
