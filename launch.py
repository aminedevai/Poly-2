"""
launch.py
=========
Unified launcher for all Polymarket Bot dashboards.

Starts:
  - Monitor dashboard  (port 8082) -> live view of main.py
  - Backtest dashboard (port 8081) -> historical strategy testing

Usage:
    python launch.py            # start both + open browser tabs
    python launch.py --monitor  # monitor only
    python launch.py --backtest # backtest only
    python launch.py --no-browser  # servers only, don't open browser
"""
import subprocess, webbrowser, time, sys, socket, argparse, os, signal

# ── Config ────────────────────────────────────────────────────────────────────
MONITOR_PORT  = 8082
BACKTEST_PORT = 8081
HOST          = "127.0.0.1"

MONITOR_URL   = f"http://{HOST}:{MONITOR_PORT}"
BACKTEST_URL  = f"http://{HOST}:{BACKTEST_PORT}"


# ── Port helpers ──────────────────────────────────────────────────────────────
def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def wait_for_port(host: str, port: int, label: str, timeout: int = 15) -> bool:
    """Poll port until open or timeout. Returns True if successful."""
    for i in range(timeout):
        if is_port_open(host, port):
            return True
        print(f"    waiting for {label} (port {port})... {i+1}s")
        time.sleep(1)
    return False


# ── Server launcher ───────────────────────────────────────────────────────────
def start_server(module: str, label: str, port: int) -> subprocess.Popen | None:
    """
    Launch a Python module as a background subprocess.
    Returns the process, or None if port is already in use.
    """
    if is_port_open(HOST, port):
        print(f"  [{label}] port {port} already open — skipping launch")
        return None

    print(f"  [{label}] starting {module} on port {port}...")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", module],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        return proc
    except Exception as e:
        print(f"  [{label}] ERROR: {e}")
        return None


# ── Log reader thread ─────────────────────────────────────────────────────────
def log_reader(proc: subprocess.Popen, label: str, color: str):
    """Print server stdout with a colored label prefix."""
    RESET = "\033[0m"
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"{color}[{label}]{RESET} {line}")
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Launch Polymarket Bot dashboards")
    parser.add_argument("--monitor",    action="store_true", help="Monitor dashboard only")
    parser.add_argument("--backtest",   action="store_true", help="Backtest dashboard only")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser tabs")
    args = parser.parse_args()

    # Default: start both
    run_monitor  = args.monitor  or (not args.monitor and not args.backtest)
    run_backtest = args.backtest or (not args.monitor and not args.backtest)

    # Windows console color fix
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    CY = "\033[96m"; GR = "\033[92m"; OR = "\033[93m"; MG = "\033[95m"; R = "\033[0m"

    print(f"\n{CY}{'='*50}{R}")
    print(f"{CY}  Polymarket Bot — Dashboard Launcher{R}")
    print(f"{CY}{'='*50}{R}\n")

    processes = []

    # ── Start Monitor ─────────────────────────────────────────────────────────
    monitor_proc = None
    if run_monitor:
        monitor_proc = start_server("monitor.server", "MONITOR", MONITOR_PORT)
        if monitor_proc:
            processes.append(monitor_proc)
            if not wait_for_port(HOST, MONITOR_PORT, "Monitor", timeout=12):
                print(f"  {OR}[MONITOR] Warning: server may not be ready{R}")
            else:
                print(f"  {GR}[MONITOR] Ready  ->  {MONITOR_URL}{R}")

    # ── Start Backtest ────────────────────────────────────────────────────────
    backtest_proc = None
    if run_backtest:
        backtest_proc = start_server("backtest.server", "BACKTEST", BACKTEST_PORT)
        if backtest_proc:
            processes.append(backtest_proc)
            if not wait_for_port(HOST, BACKTEST_PORT, "Backtest", timeout=12):
                print(f"  {OR}[BACKTEST] Warning: server may not be ready{R}")
            else:
                print(f"  {GR}[BACKTEST] Ready  ->  {BACKTEST_URL}{R}")

    # ── Open browser ──────────────────────────────────────────────────────────
    if not args.no_browser:
        time.sleep(0.5)
        if run_monitor and is_port_open(HOST, MONITOR_PORT):
            print(f"\n  Opening monitor dashboard...")
            webbrowser.open(MONITOR_URL)
            time.sleep(0.4)
        if run_backtest and is_port_open(HOST, BACKTEST_PORT):
            print(f"  Opening backtest dashboard...")
            webbrowser.open(BACKTEST_URL)

    # ── Stream logs ───────────────────────────────────────────────────────────
    import threading

    if monitor_proc:
        t = threading.Thread(
            target=log_reader,
            args=(monitor_proc, "MONITOR", GR),
            daemon=True,
        )
        t.start()

    if backtest_proc:
        t = threading.Thread(
            target=log_reader,
            args=(backtest_proc, "BACKTEST", MG),
            daemon=True,
        )
        t.start()

    print(f"\n{CY}  Dashboards running.{R}")
    if run_monitor:  print(f"  Monitor  : {CY}{MONITOR_URL}{R}")
    if run_backtest: print(f"  Backtest : {MG}{BACKTEST_URL}{R}")
    print(f"\n  {OR}Tip: run 'python main.py' in a separate terminal to start the bot{R}")
    print(f"  Press Ctrl+C to stop all servers\n")

    # ── Keep alive ────────────────────────────────────────────────────────────
    try:
        while True:
            time.sleep(1)
            # Restart crashed servers
            for proc in list(processes):
                if proc.poll() is not None:
                    processes.remove(proc)
                    print(f"  {OR}A server crashed (exit {proc.returncode}) — check logs{R}")
    except KeyboardInterrupt:
        print(f"\n{OR}  Stopping servers...{R}")
        for proc in processes:
            try:
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        print(f"{GR}  All servers stopped.{R}\n")


if __name__ == "__main__":
    main()
