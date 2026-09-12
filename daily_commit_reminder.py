#!/usr/bin/env python3
"""
Daily GitHub Commit Reminder
Checks recent commits and reminds user to commit if inactive.
Run via Windows Task Scheduler daily at 9 AM.
"""
import subprocess
import json
from datetime import datetime, timedelta

REPOS = [
    "DARKPHOENIX2530/phoenix-assistant",
    "DARKPHOENIX2530/phoenix-auto-updater"
]

def get_recent_commits(repo, days=1):
    """Get commits from last N days."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits", "--jq", f'[.[] | select(.commit.author.date >= "{(datetime.now() - timedelta(days=days)).isoformat()}")]'],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return []

def main():
    today_commits = {}
    total_today = 0
    
    for repo in REPOS:
        commits = get_recent_commits(repo, days=1)
        today_commits[repo] = len(commits)
        total_today += len(commits)
    
    if total_today == 0:
        print("⚠️  DAILY REMINDER: No commits today to phoenix-assistant or phoenix-auto-updater!")
        print("   Push some code to keep the streak alive. Even a small fix counts.")
    else:
        print(f"✅ Good job! {total_today} commit(s) today:")
        for repo, count in today_commits.items():
            if count > 0:
                print(f"   • {repo}: {count} commit(s)")

if __name__ == "__main__":
    main()
