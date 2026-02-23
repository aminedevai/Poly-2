"""
launch.py
=========
Launches everything: main.py bot + monitor dashboard + backtest dashboard.

Usage:
    python launch.py                # bot + both dashboards + browser
    python launch.py --no-bot       # dashboards only (bot already running)
    python launch.py --monitor      # monitor dashboard only
    python launch.py --backtest     # backtest dashboard only
    python launch.py --no-browser   # no auto-open browser
"""
import subprocess, webbrowser, time, sys, socket, argparse, os, threading, signal

# ── Config ────────────────────────────────────────────────────────────────────
MONITOR_PORT  = 8082
BACKTEST_PORT = 8081
HOST          = "127.0.0.1"
MONITOR_URL   = f"http://{HOST}:{MONITOR_PORT}"
BACKTEST_URL  = f"http://{HOST}:{BACKTEST_PORT}"

# Colors
CY = "\033[96m"; GR = "\033[92m"; OR = "\033[93m"; MG = "\033[95m"
RE = "\033[91m"; DM = "\033[90m"; R  = "\033[0m"

# Shared env that forces UTF-8 in every child process — avoids the
# io.TextIOWrapper conflict that caused "I/O operation on closed file"
_child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def wait_for_port(port: int, label: str, timeout: int = 15) -> bool:
    for i in range(timeout):
        if is_port_open(port):
            return True
        print(f"    [{label}] waiting... {i+1}s")
        time.sleep(1)
    return False


def _popen(cmd: list, label: str) -> subprocess.Popen:
    """
    Spawn a subprocess with stdout piped back to us.
    Key fix: use -u (unbuffered) + PYTHONIOENCODING env instead of
    wrapping sys.stdout inside the child (which breaks when stdout is a pipe).
    """
    return subprocess.Popen(
        [sys.executable, "-u"] + cmd,   # -u = unbuffered stdout/stderr
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_child_env,
        # On Windows, create new process group so Ctrl+C propagates cleanly
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                       if sys.platform == "win32" else 0),
    )


def log_reader(proc: subprocess.Popen, label: str, color: str):
    """Stream subprocess stdout to our console with a label prefix."""
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                print(f"{color}[{label}]{R} {line}", flush=True)
    except Exception:
        pass


# ── Process launchers ─────────────────────────────────────────────────────────
def start_server(module: str, label: str, port: int,
                 processes: list, threads: list, color: str):
    if is_port_open(port):
        print(f"  {DM}[{label}] port {port} already in use — skipping{R}")
        return

    print(f"  {color}[{label}]{R} starting on port {port}...")
    proc = _popen(["-m", module], label)
    processes.append((label, proc))

    if wait_for_port(port, label, timeout=15):
        print(f"  {GR}[{label}] ready  ->  http://{HOST}:{port}{R}")
    else:
        print(f"  {OR}[{label}] slow to start — continuing anyway{R}")

    t = threading.Thread(target=log_reader, args=(proc, label, color), daemon=True)
    t.start()
    threads.append(t)


def start_bot(processes: list, threads: list):
    """Launch main.py in a separate visible terminal window if possible,
    otherwise as a background subprocess with log streaming."""
    print(f"  {CY}[BOT]{R} starting main.py...")

    if sys.platform == "win32":
        # Open in a new CMD window so the bot's terminal UI is visible
        try:
            proc = subprocess.Popen(
                ["cmd", "/c", "start", "cmd", "/k",
                 sys.executable, "-u", "main.py"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                env=_child_env,
            )
            print(f"  {GR}[BOT]{R} launched in new terminal window")
            # Don't add to processes list — we don't own the new window
            return
        except Exception as e:
            print(f"  {OR}[BOT] new window failed ({e}), running inline{R}")

    # Fallback: run inline and stream logs
    proc = _popen(["main.py"], "BOT")
    processes.append(("BOT", proc))
    t = threading.Thread(target=log_reader, args=(proc, "BOT", CY), daemon=True)
    t.start()
    threads.append(t)
    print(f"  {GR}[BOT]{R} running (logs streamed below)")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Enable Windows ANSI colors
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Launch Polymarket Bot")
    parser.add_argument("--no-bot",     action="store_true", help="Skip launching main.py")
    parser.add_argument("--monitor",    action="store_true", help="Monitor dashboard only")
    parser.add_argument("--backtest",   action="store_true", help="Backtest dashboard only")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    # Determine what to run
    dash_only    = args.monitor or args.backtest
    run_monitor  = args.monitor  or not dash_only
    run_backtest = args.backtest or not dash_only
    run_bot      = not args.no_bot and not dash_only

    print(f"\n{CY}{'='*52}{R}")
    print(f"{CY}  Polymarket Bot Launcher{R}")
    print(f"{CY}{'='*52}{R}\n")

    processes: list = []   # list of (label, Popen)
    threads:   list = []

    # ── 1. Bot ────────────────────────────────────────────────────────────────
    if run_bot:
        start_bot(processes, threads)
        time.sleep(1)   # give bot a moment before dashboards start

    # ── 2. Monitor server ─────────────────────────────────────────────────────
    if run_monitor:
        start_server("monitor.server", "MONITOR", MONITOR_PORT,
                     processes, threads, GR)

    # ── 3. Backtest server ────────────────────────────────────────────────────
    if run_backtest:
        start_server("backtest.server", "BACKTEST", BACKTEST_PORT,
                     processes, threads, MG)

    # ── 4. Open browser ───────────────────────────────────────────────────────
    if not args.no_browser:
        time.sleep(0.3)
        if run_monitor and is_port_open(MONITOR_PORT):
            webbrowser.open(MONITOR_URL)
            time.sleep(0.3)
        if run_backtest and is_port_open(BACKTEST_PORT):
            webbrowser.open(BACKTEST_URL)

    # ── Status summary ────────────────────────────────────────────────────────
    print(f"\n{CY}  Everything running:{R}")
    if run_bot:      print(f"  Bot      : {CY}main.py{R} (separate window or streamed below)")
    if run_monitor:  print(f"  Monitor  : {GR}{MONITOR_URL}{R}")
    if run_backtest: print(f"  Backtest : {MG}{BACKTEST_URL}{R}")
    print(f"\n  {DM}Press Ctrl+C to stop all{R}\n")

    # ── Keep alive + crash detection ──────────────────────────────────────────
    try:
        while True:
            time.sleep(2)
            for label, proc in list(processes):
                rc = proc.poll()
                if rc is not None:
                    processes.remove((label, proc))
                    print(f"  {OR}[{label}] exited (code {rc}){R}")
    except KeyboardInterrupt:
        print(f"\n{OR}  Stopping...{R}")
        for label, proc in processes:
            try:
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                proc.wait(timeout=4)
            except Exception:
                try: proc.kill()
                except Exception: pass
        print(f"{GR}  All stopped.{R}\n")


if __name__ == "__main__":
    main()
