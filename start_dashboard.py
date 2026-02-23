import subprocess
import webbrowser
import time
import sys
import socket

# Configured based on your specific CLI settings
PORT = 8081
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}"


def is_port_open(host, port):
    """Check if the server is actually ready to receive traffic."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def launch_dashboard():
    print(f"--- Launching Poly 2 Dashboard ---")

    try:
        # Start the server: python -m backtest.server
        process = subprocess.Popen(
            [sys.executable, "-m", "backtest.server"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        print(f"Waiting for server to wake up on port {PORT}...")

        # Poll the port for up to 15 seconds
        for _ in range(15):
            if is_port_open(HOST, PORT):
                print("✅ Server is LIVE!")
                break
            time.sleep(1)
        else:
            print(f"❌ Timeout: Server didn't start on {PORT}. Check for errors above.")
            return

        # Open the browser to the working dashboard
        webbrowser.open(URL)

        print("\n--- Server Logs (Press Ctrl+C to stop) ---")
        for line in process.stdout:
            print(line, end="")

    except KeyboardInterrupt:
        print("\nStopping server...")
        process.terminate()
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    launch_dashboard()