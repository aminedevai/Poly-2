"""
backtest/server.py
==================
Tiny HTTP server that serves the backtest dashboard and handles API calls.

Endpoints:
  GET  /                           - backtest/dashboard.html
  GET  /api/datasets               - list available data files
  GET  /api/results                - list saved result files
  POST /api/fetch   {days, from, to} - fetch historical data
  POST /api/run     {strategy, file, capital, bet, trigger, min_vol}
                                   - run backtest, return results JSON
  GET  /api/result/{filename}      - load a saved result JSON

Run: python -m backtest.server
"""
import json, os, sys, threading

# Force UTF-8 output on Windows (fixes charmap codec errors)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.fetch_data import fetch_range, save, load, list_saved, DATA_DIR
from backtest.engine     import run as run_engine
from backtest.strategies import STRATEGIES

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
DASH_FILE   = os.path.join(os.path.dirname(__file__), "dashboard.html")
PORT        = 8081

# In-memory job state
_job = {"status": "idle", "progress": "", "result": None, "error": ""}


def _run_job(params: dict):
    global _job
    _job = {"status": "running", "progress": "Starting...", "result": None, "error": ""}

    try:
        now = datetime.now(timezone.utc)

        # -- Get data ----------------------------------------------------------
        if params.get("file"):
            _job["progress"] = f"Loading data from {params['file']}..."
            markets = load(params["file"])
        else:
            days  = int(params.get("days", 7))
            from_ = params.get("date_from", "")
            to_   = params.get("date_to",   "")
            if from_:
                start = datetime.fromisoformat(from_).replace(tzinfo=timezone.utc)
                end   = datetime.fromisoformat(to_).replace(tzinfo=timezone.utc) if to_ else now
            else:
                start = now - timedelta(days=days)
                end   = now

            label  = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
            cached = os.path.join(DATA_DIR, f"markets_{label}.json")

            if os.path.exists(cached) and not params.get("refetch"):
                _job["progress"] = f"Using cached data ({label})..."
                markets = load(cached)
            else:
                _job["progress"] = f"Fetching {days} days of data..."
                markets = fetch_range(start, end)
                save(markets, label)

        _job["progress"] = f"Running backtest on {len(markets)} markets..."

        # -- Run strategy ------------------------------------------------------
        strat_name = params.get("strategy", "mean_reversion")
        capital    = float(params.get("capital", 100.0))
        bet        = float(params.get("bet",     10.0))
        trigger    = float(params.get("trigger", 0.05))
        min_vol    = float(params.get("min_vol", 100.0))

        strategies_to_run = (
            list(STRATEGIES.keys()) if strat_name == "all"
            else [strat_name]
        )

        results = []
        for name in strategies_to_run:
            cls   = STRATEGIES[name]
            strat = (cls(trigger_dist=trigger, bet_size=bet, min_volume=min_vol)
                     if name == "mean_reversion"
                     else cls(bet_size=bet, min_volume=min_vol))
            res = run_engine(markets, strat, starting_capital=capital)
            results.append(res.to_dict())

        # -- Save result -------------------------------------------------------
        os.makedirs(RESULTS_DIR, exist_ok=True)
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"result_{strat_name}_{ts}.json"
        path  = os.path.join(RESULTS_DIR, fname)
        out   = results[0] if len(results) == 1 else {"comparison": results}
        with open(path, "w") as f:
            json.dump(out, f, indent=2)

        _job["status"]   = "done"
        _job["result"]   = out
        _job["progress"] = f"Done — {results[0]['n_signals']} signals"

    except Exception as e:
        import traceback
        _job["status"] = "error"
        _job["error"]  = str(e)
        _job["progress"] = f"Error: {e}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass   # suppress access log spam

    def _send(self, code: int, body: str, ctype="application/json"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(data))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/backtest", "/dashboard"):
            if os.path.exists(DASH_FILE):
                with open(DASH_FILE, encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                self._send(404, json.dumps({"error": "dashboard.html not found"}))

        elif path == "/api/datasets":
            files = list_saved()
            self._send(200, json.dumps({"files": files}))

        elif path == "/api/results":
            os.makedirs(RESULTS_DIR, exist_ok=True)
            files = sorted(f for f in os.listdir(RESULTS_DIR) if f.endswith(".json"))
            self._send(200, json.dumps({"files": files}))

        elif path.startswith("/api/result/"):
            fname = path.replace("/api/result/", "")
            fpath = os.path.join(RESULTS_DIR, fname)
            if os.path.exists(fpath):
                with open(fpath) as f:
                    self._send(200, f.read())
            else:
                self._send(404, json.dumps({"error": "not found"}))

        elif path == "/api/status":
            self._send(200, json.dumps({
                "status":   _job["status"],
                "progress": _job["progress"],
                "error":    _job["error"],
                "has_result": _job["result"] is not None,
            }))

        elif path == "/api/latest_result":
            if _job["result"]:
                self._send(200, json.dumps(_job["result"]))
            else:
                self._send(404, json.dumps({"error": "No result yet"}))

        elif path == "/api/strategies":
            self._send(200, json.dumps({
                "strategies": [
                    {"id": "mean_reversion",  "label": "Mean Reversion"},
                    {"id": "sniper",          "label": "Volume Spike Sniper"},
                    {"id": "control_down",    "label": "Control: Always Bet DOWN"},
                    {"id": "all",             "label": "Run All & Compare"},
                ]
            }))

        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path   = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"
        try:
            params = json.loads(body)
        except:
            params = {}

        if path == "/api/run":
            if _job["status"] == "running":
                self._send(409, json.dumps({"error": "A job is already running"}))
                return
            thread = threading.Thread(target=_run_job, args=(params,), daemon=True)
            thread.start()
            self._send(200, json.dumps({"status": "started"}))

        elif path == "/api/fetch":
            if _job["status"] == "running":
                self._send(409, json.dumps({"error": "A job is already running"}))
                return
            def fetch_job():
                global _job
                _job = {"status": "running", "progress": "Fetching data...",
                        "result": None, "error": ""}
                try:
                    now = datetime.now(timezone.utc)
                    days  = int(params.get("days", 7))
                    from_ = params.get("date_from", "")
                    to_   = params.get("date_to",   "")
                    if from_:
                        start = datetime.fromisoformat(from_).replace(tzinfo=timezone.utc)
                        end   = datetime.fromisoformat(to_).replace(tzinfo=timezone.utc) if to_ else now
                    else:
                        start = now - timedelta(days=days)
                        end   = now
                    label   = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
                    markets = fetch_range(start, end,
                        delay=float(params.get("delay", 0.15)))
                    path_   = save(markets, label)
                    _job["status"]   = "done"
                    _job["progress"] = f"Fetched {len(markets)} markets -> {os.path.basename(path_)}"
                    _job["result"]   = {"n_fetched": len(markets), "file": os.path.basename(path_)}
                except Exception as e:
                    _job["status"] = "error"
                    _job["error"]  = str(e)
            threading.Thread(target=fetch_job, daemon=True).start()
            self._send(200, json.dumps({"status": "started"}))

        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    os.makedirs(DATA_DIR,    exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  Backtest server running at http://localhost:{PORT}")
    print(f"  Open in browser:  http://localhost:{PORT}/")
    print(f"  Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
