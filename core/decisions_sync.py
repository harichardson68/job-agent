"""
core/decisions_sync.py
=======================
Keeps job_decisions.json in sync across machines/environments that share it
(local desktop runs, GitHub Actions cloud runs). The file lives in a separate
git repo (job-search-hans) from job-agent's own code, so this is plain git
pull/push against that repo's directory — not a job-agent concern.

GitHub Actions doesn't need this: actions/checkout already gives it a fresh
clone every run, and the workflow's own steps commit+push at the end. This
module only matters for local execution, where the same checkout sits on
disk indefinitely and can drift out of date between runs.

Both functions are best-effort and never raise — a stale read or a failed
push shouldn't block a local run over a network hiccup.
"""

import os
import subprocess

from tools.score import DECISIONS_PATH


def _repo_dir() -> str:
    """Directory containing job_decisions.json — the Jobsearch repo root."""
    return os.path.dirname(DECISIONS_PATH)


def _is_git_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def _skip() -> bool:
    """True when the workflow itself already owns pull/push for this repo
    (GitHub Actions sets GITHUB_ACTIONS=true). Its own steps configure git
    identity and push using JOBSEARCH_PAT after the agent runs — doing the
    same thing here first would collide with that (no commit identity set
    yet, and a double-commit attempt)."""
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def pull_before_run() -> None:
    """Pull the decisions repo before reading job_decisions.json, so dedup
    sees whatever the most recent run (local or cloud) already decided."""
    if _skip():
        return
    repo = _repo_dir()
    if not _is_git_repo(repo):
        return  # repo not present locally
    try:
        result = subprocess.run(
            ["git", "pull"], cwd=repo, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"[WARN] decisions_sync: git pull failed in {repo}: "
                  f"{(result.stderr or result.stdout).strip()}")
    except Exception as e:
        print(f"[WARN] decisions_sync: could not pull {repo}: {e}")


def push_after_run() -> None:
    """Commit + push job_decisions.json if it changed. If the push is
    rejected (remote advanced since pull_before_run — e.g. a cloud run
    committed in the meantime), pull once and retry rather than giving up
    silently or force-pushing over it."""
    if _skip():
        return
    repo = _repo_dir()
    if not _is_git_repo(repo):
        return
    try:
        subprocess.run(["git", "add", "job_decisions.json"], cwd=repo, check=True)

        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=repo,
        )
        if staged.returncode == 0:
            return  # nothing changed — nothing to push

        subprocess.run(
            ["git", "commit", "-m", "Local agent run — update job decisions history"],
            cwd=repo, check=True,
        )

        for attempt in range(2):
            push = subprocess.run(["git", "push"], cwd=repo, capture_output=True, text=True)
            if push.returncode == 0:
                return
            if attempt == 0:
                subprocess.run(
                    ["git", "pull", "--rebase"], cwd=repo, capture_output=True, text=True,
                )

        print(f"[WARN] decisions_sync: push failed after retry in {repo}: "
              f"{(push.stderr or push.stdout).strip()}")
    except Exception as e:
        print(f"[WARN] decisions_sync: could not commit/push {repo}: {e}")
