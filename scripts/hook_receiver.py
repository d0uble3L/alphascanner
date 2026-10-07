"""Tiny local webhook receiver for trying out alerts.

Prints each alert POST it receives and answers 200. Point an alert at it with
ALPHASCANNER_ALERTS_ALLOW_PRIVATE_WEBHOOKS=true (it listens on localhost):

    python scripts/hook_receiver.py            # default port 9000
    python scripts/hook_receiver.py 9100       # another port
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        print("\n" + body.get("text", "(no text field)"), flush=True)
        for m in body.get("new_matches", []):
            volume = m.get("total_volume") or 0
            print(
                f"  {str(m.get('symbol', '?')).upper():8} vol={volume:,.0f}  "
                f"surge={m.get('volume_surge')}",
                flush=True,
            )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    print(f"Listening on http://127.0.0.1:{port} ...", flush=True)
    HTTPServer(("127.0.0.1", port), Hook).serve_forever()
