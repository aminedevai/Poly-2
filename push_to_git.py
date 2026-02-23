import subprocess
from datetime import datetime
import os


def github_push():
    # You're already in the right folder, so no need to change directory!
    print(f"📁 Current folder: {os.getcwd()}")

    # Check if valid git repo
    if not os.path.exists(".git"):
        print("❌ ERROR: No .git folder found here!")
        return

    # Get current branch
    branch_result = subprocess.run(["git", "branch", "--show-current"],
                                   capture_output=True, text=True)
    branch = branch_result.stdout.strip()
    print(f"🌿 Branch: {branch}")

    # Prepare commit message
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"Update: {timestamp}"

    try:
        print("\n--- Starting Git Push ---")

        # Step 1: Add ALL files (new, modified, deleted)
        print("1️⃣ Adding all files...")
        subprocess.run(["git", "add", "-A"], check=True)

        # Step 2: Check if there's anything to commit
        status = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if status.returncode == 0:
            print("⚠️ No changes to commit (everything up to date)")
            return

        # Step 3: Commit
        print(f"2️⃣ Committing: '{commit_msg}'")
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)

        # Step 4: Push
        print(f"3️⃣ Pushing to origin {branch}...")
        subprocess.run(["git", "push", "origin", branch], check=True)

        print(f"\n✅ SUCCESS! Pushed to GitHub")

    except subprocess.CalledProcessError as e:
        print(f"\n❌ FAILED: {e}")


if __name__ == "__main__":
    github_push()