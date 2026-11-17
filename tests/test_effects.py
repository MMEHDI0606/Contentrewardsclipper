"""The edit effects: cutting pauses, punch-in zoom, freeze frame with a slow-motion replay, and the streamer's name
tag. Checks the plan of each clip without ffmpeg, then makes one short clip with every effect and checks its length.

    python tests/test_effects.py"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-fx-")
sys.path.insert(0, ".")
from studio import render  # noqa: E402

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


def said(*spans, peak=None):
    """Words every half second through each (from, to) span; the word nearest `peak` is "WINS"."""
    out = []
    for a, b in spans:
        t = a
        while t < b:
            out.append({"w": "WINS" if peak is not None and abs(t - peak) < 0.2 else "word", "s": t, "e": t + 0.4})
            t += 0.5
    return out


words = said((2, 6), (9, 13), (15, 20), peak=18.5)
clip = {"start": 1.0, "end": 24.0, "highlight_words": ["wins"]}

plain = render.timeline(clip, words, 1.0, 24.0)
check("without effects the clip is one piece, as before", plain == [(1.0, 24.0, 1.0, "main")], plain)

trim = render.timeline(dict(clip, trim_pauses=True), words, 1.0, 24.0)
kept = sum(b - a for a, b, *_ in trim)
check("pauses longer than the limit are cut out", len(trim) == 3 and abs(kept - 17.40) < 0.01, trim)
check("no word is lost when pauses are cut",
      all(any(a <= w["s"] and w["e"] <= b for a, b, *_ in trim) for w in words))
check("the silence after the last word stays (the reaction)", trim[-1][1] == 24.0)
check("short pauses are left alone", render.timeline(dict(clip, trim_pauses=True), said((1, 24)), 1.0, 24.0) == [(1.0, 24.0, 1.0, "main")])

rep = render.timeline(dict(clip, replay=True), words, 1.0, 24.0)
kinds = [p[0] if p[0] == "freeze" else p[3] for p in rep]
check("a replay freezes on the moment, replays it slowly, then carries on", kinds == ["main", "freeze", "replay", "main"], rep)
check("the moment is the last highlighted word", abs(rep[1][1] - 18.9) < 0.01, rep[1])
check("the replay is in slow motion", rep[2][2] == render.REPLAY["speed"])
check("no replay when the moment is right at the start",
      all(p[0] != "freeze" for p in render.timeline({"start": 0.0, "end": 10.0, "highlight_words": ["wins"]}, said((0, 1), peak=0.1), 0.0, 10.0)))

shown, total, banners, to_out = render._place(rep, words)
check("the finished length counts the freeze and the slow replay", abs(total - (23 + 0.7 + 4.0)) < 0.01, total)
check("the replay text stays up through the freeze and the replay", banners and abs(banners[0][2] - banners[0][1] - 4.7) < 0.01, banners)
check("captions are not shown twice during the replay", len(shown) == len(words), (len(shown), len(words)))
check("words after the replay are moved later by its length", abs(to_out(20.0) - (19.0 + 4.7)) < 0.01, to_out(20.0))

ass = render.build_ass([], 0, 10, "HOOK", "bounce", [], 300, 1380, "Paid partnership", "", "kaicenat")
first = [l for l in ass.splitlines() if l.startswith("Dialogue") and ",0:00:00.00," in l]
check("the streamer's name tag is on screen from the first frame, under the disclosure",
      any("@kaicenat" in l for l in first) and any("Paid partnership" in l for l in first))

if shutil.which("ffmpeg"):
    work = Path(tempfile.mkdtemp(prefix="pc-fxr-"))
    src = work / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=12", "-f", "lavfi",
                    "-i", "sine=f=440:d=12", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    w = said((1, 3), (5, 9), peak=8.0)
    c = {"start": 0.5, "end": 11.0, "hook": "HE WINS", "layout": "crop", "highlight_words": ["wins"], "disclosure": "#ad",
         "name_tag": "kai", "trim_pauses": True, "punch": True, "replay": True, "teaser": 2.0}
    expect = render._place(render.timeline(c, w, 0.5, 11.0), w)[1]
    out = render.render_clip(src, work / "out", "fx", c, w, 640, 360)
    got = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(work / "out" / out["file"])], capture_output=True, text=True).stdout)
    check("a clip with every effect renders at the planned length", abs(got - expect) < 0.2, (got, expect))
    shutil.rmtree(work, ignore_errors=True)
else:
    print("SKIP rendering: ffmpeg is not installed here")

print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
