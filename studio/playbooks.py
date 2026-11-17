"""Playbooks: named recipes for how a kind of moment is presented (hook, edit, captions). They are data files in
the playbooks/ folder, so they can be changed without touching code. The AI suggests one per clip; the rules here make
sure no account looks templated (no playbook three times in a row, caption styles take turns)."""
import json
import random
from pathlib import Path

from . import config, render

DIR = Path(config.env("PEAKCUT_PLAYBOOKS") or config.ROOT / "playbooks")
_cache: dict = {"m": None, "all": {}}


def library() -> dict:
    """id -> playbook, read again whenever a file in the folder changes."""
    files = sorted(DIR.glob("*.json")) if DIR.is_dir() else []
    stamp = tuple((f.name, f.stat().st_mtime) for f in files)
    if _cache["m"] != stamp:
        got = {}
        for f in files:
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(p, dict) and p.get("id"):
                    got[str(p["id"])] = p
            except ValueError:
                continue
        _cache.update(m=stamp, all=got)
    return _cache["all"]


def structures() -> dict:
    return {k: p for k, p in library().items() if p.get("kind", "structure") == "structure"}


def menu() -> str:
    """The structure playbooks as the AI sees them: id, what each is for."""
    return "\n".join(f"- {k}: {p.get('about', '')} Suits: {', '.join(p.get('when', {}).get('categories') or ['anything'])}. "
                     f"Hook: {(p.get('hook') or {}).get('rules', '')}" for k, p in structures().items())


def hook_rules(pid) -> str:
    p = structures().get(pid) or {}
    return (p.get("hook") or {}).get("rules", "")


def _fits(p, clip, has_chat) -> bool:
    w = p.get("when") or {}
    cats = w.get("categories") or []
    if cats and clip.get("category") not in cats:
        return False
    if w.get("needs") == "chat" and not has_chat:
        return False
    dur = clip["end"] - clip["start"]
    return not ((w.get("max_len") and dur > w["max_len"] + 5) or (w.get("min_len") and dur < w["min_len"] - 5))


def choose(clip, history, creator=None, has_chat=False, rng=random):
    """Settle the clip's playbook, variant and caption style.

    history: this creator's earlier clips, oldest first (only their playbook fields are read).
    The AI's suggestion is kept when it fits; experimental ones run on their share of clips only; the same playbook
    and variant never runs more than twice in a row; the caption style differs from the clip before."""
    lib = structures()
    wanted = clip.get("playbook") if clip.get("playbook") in lib else "plain"
    p = lib.get(wanted) or {}
    reason = clip.get("playbook_reason") or ""
    if wanted != "plain" and (not _fits(p, clip, has_chat) or rng.random() > float(p.get("share", 1.0))):
        reason = f"{p.get('name', wanted)} did not fit or was held back this time; Plain used instead."
        wanted = "plain"
    last = [(h.get("playbook"), h.get("variant")) for h in history[-2:]]
    variants = (lib.get(wanted) or {}).get("variants") or [{"id": "a"}]
    variant = variants[0]["id"]
    if len(last) == 2 and last[0] == last[1] and last[0][0] == wanted:
        others = [v["id"] for v in variants if v["id"] != last[0][1]]
        if others:
            variant = others[0]
        else:                               # no other variant: another playbook that fits, else Plain
            alt = next((k for k, q in lib.items() if k not in (wanted, "plain") and _fits(q, clip, has_chat)
                        and float(q.get("share", 1.0)) >= 1.0), "plain")
            if alt != wanted:
                reason = f"Rotated: {lib[wanted].get('name', wanted)} ran twice in a row."
                wanted, variant = alt, ((lib.get(alt) or {}).get("variants") or [{"id": "a"}])[0]["id"]
    p = lib.get(wanted) or {}
    v = next((x for x in p.get("variants") or [] if x.get("id") == variant), {})
    style = (v.get("captions") or {}).get("style") or (p.get("captions") or {}).get("style") or clip.get("style") or "bounce"
    prev_style = history[-1].get("style") if history else None
    if style == prev_style:
        style = next((s for s in render.STYLES if s != prev_style), style)
    edit = dict(p.get("edit") or {})
    layout = edit.get("layout") or clip.get("layout") or "crop"
    if creator and creator.get("facecam"):
        lay = "full_cam" if clip.get("category") in ((library().get("full_cam") or {}).get("when") or {}).get("categories", []) else "reaction_stack"
        layout = ((library().get(lay) or {}).get("edit") or {}).get("layout", layout)
    clip.update(playbook=wanted, variant=variant, playbook_reason=reason[:200], style=style, layout=layout,
                teaser=float(edit.get("teaser") or 0), context_line=bool(edit.get("context_line")),
                trim_pauses=bool(edit.get("trim_pauses")), punch=bool(edit.get("punch")), replay=bool(edit.get("replay")))
    return clip
