import subprocess
from datetime import datetime
import os


def github_push_force():
    print(f"📁 Current folder: {os.getcwd()}")

    try:
        print("\n" + "=" * 50)
        print("🚀 FORCE PUSHING ALL FILES")
        print("=" * 50)

        # 1. Check git status (see what's not pushed)
        print("\n1️⃣ Current Git Status:")
        result = subprocess.run(["git", "status"], capture_output=True, text=True)
        print(result.stdout)

        # 2. Add EVERYTHING (including ignored files if needed)
        print("\n2️⃣ Force adding ALL files...")
        subprocess.run(["git", "add", "-A"], check=True)

        # 3. Check what's staged
        print("\n3️⃣ Files ready to commit:")
        result = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                capture_output=True, text=True)
        staged_files = result.stdout.strip()

        if not staged_files:
            print("⚠️ No files staged! Checking for untracked files...")
            subprocess.run(["git", "status", "--short"])

            # Try to see what files exist locally vs remote
            print("\n📂 Local files in directory:")
            for item in os.listdir("."):
                if not item.startswith('.') or item == '.env':
                    print(f"   {item}")
            return

        print(staged_files)

        # 4. Commit with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_msg = f"Full project update: {timestamp}"

        print(f"\n4️⃣ Committing: '{commit_msg}'")
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)

        # 5. Get current branch
        branch_result = subprocess.run(["git", "branch", "--show-current"],
                                       capture_output=True, text=True)
        branch = branch_result.stdout.strip()

        # 6. FORCE PUSH (overwrite remote if needed)
        print(f"\n5️⃣ Force pushing to origin/{branch}...")
        subprocess.run(["git", "push", "--force", "origin", branch], check=True)

        print(f"\n✅ SUCCESS! All files pushed to GitHub")
        print(f"🔗 https://github.com/aminedevai/Poly-2")

    except subprocess.CalledProcessError as e:
        print(f"\n❌ ERROR: {e}")
        print("\n🔧 Try these manual commands:")
        print("   git add -A")
        print("   git commit -m 'update'")
        print("   git push origin main --force")


if __name__ == "__main__":
    github_push_force()