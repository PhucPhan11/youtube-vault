"""Stop hook: run the test suite before Claude finishes, if code changed.

Exit codes (Claude Code hook protocol):
  0 -> allow Claude to stop
  2 -> block the stop; stderr is fed back to Claude so it fixes the failures

Skips the run when:
  - ``stop_hook_active`` is true (Claude is already continuing because of this
    hook; prevents an endless loop), or
  - nothing under src/, tests/, pyproject.toml or uv.lock differs from HEAD.

Stdlib only. Invoked through ``uv run`` so it uses the project's .venv.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WATCHED = ["src", "tests", "pyproject.toml", "uv.lock"]
TAIL_LINES = 60


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    if payload.get("stop_hook_active"):
        return 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *WATCHED],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if status.returncode == 0 and not status.stdout.strip():
        return 0  # no relevant changes -> nothing to verify

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        return 0

    output = (result.stdout + result.stderr).strip().splitlines()
    tail = "\n".join(output[-TAIL_LINES:])
    sys.stderr.write(
        "Tests are failing (uv run pytest). Fix them before finishing:\n\n" + tail + "\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
