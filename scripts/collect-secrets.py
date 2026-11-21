"""Gathers every secret Clipping Studio uses into secrets/secrets.json (shape: secrets.example.json), to back up
or move to another computer. Prints which ones it found, never their values.

    python scripts/collect-secrets.py
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def read(path, binary=False):
    try:
        return path.read_bytes() if binary else path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


out = json.loads((ROOT / "secrets.example.json").read_text(encoding="utf-8"))
env = read(ROOT / ".env") or ""
m = re.search(r"^PEAKCUT_PUBLIC_URL=(.+)$", env, re.M)
out["public_url"]["value"] = m.group(1).strip() if m else ""

ngrok = read(Path(os.environ.get("LOCALAPPDATA", "")) / "ngrok" / "ngrok.yml") or ""
m = re.search(r"authtoken:\s*(\S+)", ngrok)
out["ngrok_authtoken"]["value"] = m.group(1) if m else ""

google = (json.loads(read(DATA / "apps.json") or "{}").get("google") or {})
out["google_app"].update(id=google.get("id", ""), secret=google.get("secret", ""))

out["accounts"]["list"] = json.loads(read(DATA / "users" / "u_local" / "accounts" / "accounts.json") or "[]")
out["brain_token"]["value"] = read(DATA / "brain.token") or ""
key = read(DATA / "link.key", binary=True)
out["link_key_hex"]["value"] = key.hex() if key else ""

dest = ROOT / "secrets" / "secrets.json"
dest.parent.mkdir(exist_ok=True)
dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"Written: {dest}")
for name in ("public_url", "ngrok_authtoken", "brain_token", "link_key_hex"):
    print(f"  {name}: {'found' if out[name]['value'] else 'MISSING'}")
print(f"  google_app: {'found' if google.get('id') and google.get('secret') else 'MISSING'}")
print(f"  accounts: {', '.join(a.get('platform', '?') + ' ' + a.get('name', '') for a in out['accounts']['list']) or 'none'}")
