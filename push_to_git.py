import subprocess
from datetime import datetime


def github_push():
    # Uses current date/time for the commit message
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"Update backtest results: {timestamp}"

    try:
        print("--- Syncing with GitHub ---")
        # 1. Stage changes
        subprocess.run(["git", "add", "."], check=True)

        # 2. Commit
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)

        # 3. Push
        # Note: Using 'origin main' as established earlier
        subprocess.run(["git", "push", "origin", "main"], check=True)

        print(f"✅ Successfully pushed to https://github.com/aminedevai/Poly-2")

    except subprocess.CalledProcessError as e:
        print(f"❌ Git Push Failed. Make sure you have no conflicts. Error: {e}")


if __name__ == "__main__":
    github_push()