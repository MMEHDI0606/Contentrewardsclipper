"""The only door from the internet into Clipping Studio, for Instagram.

Instagram needs a public https address for two things: sending you back after you connect your account, and
fetching each clip while it is posted. A Cloudflare tunnel gives that address and points here; this passes on
just those two kinds of request to the app and answers "not found" to everything else, so the dashboard itself
stays private even though sign-in is off.

Run:  python scripts/public-gate.py          (listens on 127.0.0.1:8790; PEAKCUT_GATE_PORT changes it)
Then: cloudflared tunnel --url http://127.0.0.1:8790
"""
import http.client
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = ("127.0.0.1", int(os.environ.get("PEAKCUT_PORT", "8765")))
APP_URL = f"http://{APP[0]}:{APP[1]}"
ALLOWED = re.compile(r"^(/oauth/instagram/callback(\?.*)?|/pub/[A-Za-z0-9_.-]+\.mp4)$")
PASS_ON = ("range", "user-agent", "if-range", "if-none-match", "if-modified-since")
SEND_BACK = ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified", "cache-control")


class Gate(BaseHTTPRequestHandler):
    def _forward(self, head):
        if not ALLOWED.match(self.path):
            self.send_error(404)
            return
        conn = http.client.HTTPConnection(*APP, timeout=120)
        headers = {k: v for k, v in self.headers.items() if k.lower() in PASS_ON}
        headers["Host"] = f"{APP[0]}:{APP[1]}"
        conn.request("HEAD" if head else "GET", self.path, headers=headers)
        r = conn.getresponse()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() in SEND_BACK:
                self.send_header(k, v)
            elif k.lower() == "location":
                # after connecting, the app sends you to its settings page: that page is on this computer only
                self.send_header(k, APP_URL + v if v.startswith("/") else v)
        self.end_headers()
        if not head:
            while chunk := r.read(64 * 1024):
                self.wfile.write(chunk)
        conn.close()

    def do_GET(self):
        self._forward(head=False)

    def do_HEAD(self):
        self._forward(head=True)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PEAKCUT_GATE_PORT", "8790"))), Gate).serve_forever()
