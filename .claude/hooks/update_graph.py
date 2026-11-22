"""Stop hook: refresh the graphify knowledge graph after code changes.

Runs ``graphify update .`` (AST-only, no LLM, incremental) so that
``graphify-out/graph.json`` and ``GRAPH_REPORT.md`` match the code and get
committed together with it.

Never blocks: always exits 0. A failure only prints a warning to stderr.

Skips the run when:
  - ``stop_hook_active`` is true (Claude is already continuing because of a hook),
  - there is no ``graphify-out/graph.json`` (run ``/graphify .`` once to build it),
  - the ``graphify`` command is not on PATH, or
  - nothing under src/, tests/ or .claude/hooks differs from HEAD.

Stdlib only. Invoked through ``uv run`` like ``run_tests.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WATCHED = ["src", "tests", ".claude/hooks"]
TIMEOUT_S = 120
TAIL_LINES = 5


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    if payload.get("stop_hook_active"):
        return 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
    if not (root / "graphify-out" / "graph.json").is_file():
        return 0
    graphify = shutil.which("graphify")
    if graphify is None:
        return 0

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *WATCHED],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if status.returncode == 0 and not status.stdout.strip():
        return 0  # no code changes -> graph is still current

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(
            [graphify, "update", "."],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"graphify update skipped: {exc}\n")
        return 0

    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-TAIL_LINES:])
        sys.stderr.write(
            "graphify update failed (graph may be stale). If code was deleted, run "
            "`graphify update . --force`.\n" + tail + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
