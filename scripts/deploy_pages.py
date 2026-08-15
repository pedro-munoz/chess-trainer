"""Build dist/ and publish it to the gh-pages branch.

    python -m scripts.deploy_pages [--dry-run] [--no-precompute] [--remote origin]

gh-pages is kept at exactly one commit: each deploy amends it and force-pushes.
Without that, republishing a 1 MB data file and a 7 MB engine every day would
grow the repository without bound, and the branch's history has no value — the
source history on main is the real record.

The build cannot run in CI: it needs data/trainer.db and the local Stockfish,
neither of which is pushed.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from chess_trainer import PROJECT_ROOT

DIST = PROJECT_ROOT / "dist"
WORKTREE = PROJECT_ROOT / ".deploy" / "gh-pages"
BRANCH = "gh-pages"


def git(*args, cwd=PROJECT_ROOT, check=True, capture=False):
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=check, text=True,
        capture_output=capture or not sys.stdout.isatty())
    return (result.stdout or "").strip()


def ensure_worktree() -> None:
    """A persistent worktree checked out on gh-pages, created on first deploy."""
    if (WORKTREE / ".git").exists():
        return
    if WORKTREE.exists():
        shutil.rmtree(WORKTREE)
    WORKTREE.parent.mkdir(parents=True, exist_ok=True)

    branches = git("branch", "--list", BRANCH, capture=True)
    if branches:
        git("worktree", "add", str(WORKTREE), BRANCH)
    else:
        # Orphan: gh-pages shares no history with main by design.
        git("worktree", "add", "--detach", str(WORKTREE))
        git("checkout", "--orphan", BRANCH, cwd=WORKTREE)
        git("rm", "-rf", "--quiet", ".", cwd=WORKTREE, check=False)


def mirror(src: Path, dst: Path) -> None:
    """Replace dst's contents with src's, leaving dst/.git alone."""
    for item in dst.iterdir():
        if item.name == ".git":
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    for item in src.iterdir():
        target = dst / item.name
        shutil.copytree(item, target) if item.is_dir() else shutil.copy2(item, target)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and stage the branch, but do not push")
    ap.add_argument("--no-precompute", action="store_true")
    args = ap.parse_args()

    export = [sys.executable, "-m", "scripts.export_static"]
    if args.no_precompute:
        export.append("--no-precompute")
    subprocess.run(export, cwd=PROJECT_ROOT, check=True)

    if not DIST.exists():
        raise SystemExit("export produced no dist/")

    ensure_worktree()
    mirror(DIST, WORKTREE)
    git("add", "-A", cwd=WORKTREE)

    if not git("status", "--porcelain", cwd=WORKTREE, capture=True):
        print("\nNothing changed since the last deploy.")
        return

    has_commit = bool(git("log", "-1", "--oneline", cwd=WORKTREE,
                          check=False, capture=True))
    message = "Published trainer build"
    if has_commit:
        git("commit", "--amend", "--no-edit", "--quiet", cwd=WORKTREE)
    else:
        git("commit", "-m", message, "--quiet", cwd=WORKTREE)

    if args.dry_run:
        print(f"\n(dry run) {BRANCH} staged in {WORKTREE}; not pushed.")
        return

    git("push", "--force", args.remote, BRANCH, cwd=WORKTREE)
    url = git("remote", "get-url", args.remote, capture=True)
    print(f"\nPublished to {BRANCH} on {url}")
    print("If this is the first deploy, set Pages to deploy from the "
          f"{BRANCH} branch (root) in the repository settings.")


if __name__ == "__main__":
    main()
