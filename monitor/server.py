"""
monitor/server.py
=================
Tiny HTTP server that serves dashboard_data.json to the live monitor dashboard.
main.py writes dashboard_data.json every poll cycle (every 5s by default).
This server reads it and streams it to the browser via polling.

Run: python -m monitor.server
  or via launch.py (which starts both monitor + backtest servers)
"""
import json, os, sys, time

# UTF-8 handled by PYTHONIOENCODING env var set in launch.py

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# dashboard_data.json lives at project root (same dir as main.py)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH_DATA    = os.path.join(PROJECT_ROOT, "dashboard_data.json")
DASH_HTML    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
PORT         = 8082


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass   # suppress access log noise

    def _send(self, code: int, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        # ── Serve the HTML dashboard ──────────────────────────────────────────
        if path in ("/", "/monitor"):
            if os.path.exists(DASH_HTML):
                with open(DASH_HTML, "rb") as f:
                    self._send(200, f.read(), "text/html")
            else:
                self._send(404, b"dashboard.html not found", "text/plain")

        # ── Serve the live data JSON ──────────────────────────────────────────
        elif path == "/api/data":
            if not os.path.exists(DASH_DATA):
                self._send(404, json.dumps({
                    "error": "dashboard_data.json not found",
                    "hint":  "Make sure main.py is running",
                }))
                return
            try:
                with open(DASH_DATA, encoding="utf-8") as f:
                    raw = f.read()
                # Validate it's valid JSON before sending
                json.loads(raw)
                self._send(200, raw.encode("utf-8"))
            except json.JSONDecodeError:
                # File is mid-write — retry once after short delay
                time.sleep(0.1)
                try:
                    with open(DASH_DATA, encoding="utf-8") as f:
                        self._send(200, f.read().encode("utf-8"))
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)}))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))

        # ── Status ping ───────────────────────────────────────────────────────
        elif path == "/api/ping":
            bot_running = os.path.exists(DASH_DATA)
            age = 0
            if bot_running:
                try:
                    age = time.time() - os.path.getmtime(DASH_DATA)
                except:
                    age = 999
            self._send(200, json.dumps({
                "server":  "monitor",
                "bot_running": bot_running and age < 30,
                "data_age_secs": round(age, 1),
            }))

        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()


def main():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"  Monitor server  ->  http://localhost:{PORT}")
    print(f"  Watching:           {DASH_DATA}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Monitor server stopped.")


if __name__ == "__main__":
    main()
