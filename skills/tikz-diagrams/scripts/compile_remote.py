#!/usr/bin/env python3
"""Push TikZ .tex to tikz-compile branch, wait for GitHub Actions, download from release.

Usage:
    python scripts/compile_remote.py diagram.tex
    python scripts/compile_remote.py diagram.tex --output ./figures/
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Remote-compile a TikZ diagram via GitHub Actions")
    parser.add_argument("texfile", type=Path, help="Path to standalone .tex file")
    parser.add_argument("--output", "-o", type=Path, default=Path.cwd(), help="Output directory for PDF/PNG (default: cwd)")
    parser.add_argument("--branch", default="tikz-compile", help="Remote compile branch (default: tikz-compile)")
    args = parser.parse_args()

    tex = args.texfile.resolve()
    if not tex.exists():
        print(f"Error: file not found: {tex}", file=sys.stderr)
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    name = tex.stem

    print(f"[1/5] Pushing {tex.name} to branch '{args.branch}' ...")

    # Save current branch and any uncommitted work
    orig_branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
    has_changes = bool(run(["git", "status", "--porcelain"], cwd=repo_root))

    if has_changes:
        run(["git", "stash", "push", "--include-untracked", "-m", "compile_remote auto stash"], cwd=repo_root)

    try:
        # Switch to orphan compile branch
        run(["git", "checkout", "--orphan", args.branch], cwd=repo_root)
        run(["git", "rm", "-rf", "--quiet", "."], cwd=repo_root)

        # Copy only the .tex file
        import shutil
        target = repo_root / tex.name
        shutil.copy2(tex, target)
        run(["git", "add", tex.name], cwd=repo_root)
        run(["git", "commit", "-m", f"compile: {name}"], cwd=repo_root)
        run(["git", "push", "--force", "origin", args.branch], cwd=repo_root)

        print(f"[2/5] Waiting for GitHub Actions to start ...")
        time.sleep(3)

        # Find the workflow run
        for _ in range(60):
            runs = run(["gh", "run", "list", "--workflow", "compile-tikz.yml", "--branch", args.branch,
                         "--limit", "1", "--json", "databaseId,status,conclusion",
                         "-q", ".[0] | \"\\(.databaseId) \\(.status) \\(.conclusion)\""], cwd=repo_root)
            if runs and runs != "null null null":
                break
            time.sleep(2)

        if not runs or runs == "null null null":
            print("Error: workflow did not start", file=sys.stderr)
            sys.exit(1)

        run_id, status, conclusion = runs.split(None, 2)
        print(f"[3/5] Run ID: {run_id}, status: {status}")

        # Wait for completion
        for _ in range(120):
            runs = run(["gh", "run", "list", "--workflow", "compile-tikz.yml", "--branch", args.branch,
                         "--limit", "1", "--json", "databaseId,status,conclusion",
                         "-q", ".[0] | \"\\(.databaseId) \\(.status) \\(.conclusion)\""], cwd=repo_root)
            if runs and runs != "null null null":
                _, status, conclusion = runs.split(None, 2)
                if status == "completed":
                    break
            time.sleep(5)
        else:
            print("Error: workflow timed out", file=sys.stderr)
            sys.exit(1)

        if conclusion != "success":
            print(f"Error: workflow failed (conclusion: {conclusion})", file=sys.stderr)
            print(f"  View: gh run view {run_id} --log", file=sys.stderr)
            sys.exit(1)

        print(f"[4/5] Compilation succeeded. Downloading from release ...")

        # Download from latest release
        out = args.output.resolve()
        out.mkdir(parents=True, exist_ok=True)
        try:
            run(["gh", "release", "download", "latest", "--pattern", f"{name}.pdf",
                 "--dir", str(out), "--clobber"], cwd=repo_root)
            run(["gh", "release", "download", "latest", "--pattern", f"{name}.png",
                 "--dir", str(out), "--clobber"], cwd=repo_root)
        except SystemExit:
            # Try artifact fallback
            print("  Release download failed, trying artifact ...")
            run(["gh", "run", "download", run_id, "--name", "tikz-output", "--dir", str(out)], cwd=repo_root)

        pdf = out / f"{name}.pdf"
        png = out / f"{name}.png"
        print(f"[5/5] Done: {pdf} / {png}")

    finally:
        # Restore original branch
        run(["git", "checkout", orig_branch], cwd=repo_root)
        if has_changes:
            run(["git", "stash", "pop"], cwd=repo_root)


if __name__ == "__main__":
    main()
