import subprocess
from datetime import datetime


def github_push():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"Update backtest results: {timestamp}"

    try:
        print("--- Syncing with GitHub ---")

        # 1. Check git status first (for debugging)
        print("\n📋 Current status:")
        subprocess.run(["git", "status"], check=True)

        # 2. Stage ALL changes (including new files, modifications, deletions)
        print("\n📦 Staging all changes...")
        result = subprocess.run(["git", "add", "-A"], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"⚠️ Git add warning: {result.stderr}")

        # 3. Check what's staged
        print("\n📋 Staged files:")
        subprocess.run(["git", "diff", "--cached", "--name-only"], check=True)

        # 4. Commit
        print(f"\n💾 Committing with message: '{commit_msg}'")
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)

        # 5. Push
        print("\n🚀 Pushing to GitHub...")
        subprocess.run(["git", "push", "origin", "main"], check=True)

        print(f"\n✅ Successfully pushed to https://github.com/aminedevai/Poly-2")

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Git Push Failed: {e}")
        print("\n🔧 Troubleshooting tips:")
        print("   • Run 'git status' manually to see what's wrong")
        print("   • Check if you're on the correct branch")
        print("   • Ensure you have internet connection")
        print("   • Verify your remote URL: git remote -v")


if __name__ == "__main__":
    github_push()