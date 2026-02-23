import http.server
import socketserver
import webbrowser
import os
import threading
import time

PORT = 8000
# Update this if your dashboard is in a subfolder, e.g., "backtest/dashboard.html"
FILE_PATH = "backtest/dashboard.html"

def start_server():
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"Serving locally at http://localhost:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    # 1. Start the server in a separate thread so it doesn't block the script
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # 2. Give the server a second to wake up
    time.sleep(1)

    # 3. Open the specific dashboard file in your default browser
    url = f"http://localhost:{PORT}/{FILE_PATH}"
    print(f"Opening dashboard: {url}")
    webbrowser.open(url)

    # 4. Keep the main script alive so the server stays up
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down server...")