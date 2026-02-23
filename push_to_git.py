import subprocess
from datetime import datetime


def github_push():
    # Get current time for the commit message
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_message = f"Auto-update: {timestamp}"

    try:
        print("--- Starting Git Push ---")
        # 1. Add all changes
        subprocess.run(["git", "add", "."], check=True)

        # 2. Commit changes
        subprocess.run(["git", "commit", "-m", commit_message], check=True)

        # 3. Push to origin main
        subprocess.run(["git", "push", "origin", "main"], check=True)

        print(f"--- Successfully pushed to GitHub at {timestamp} ---")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred during git operations: {e}")


if __name__ == "__main__":
    github_push()