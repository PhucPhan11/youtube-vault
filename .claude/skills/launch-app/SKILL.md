---
name: launch-app
description: Launch the youtube-vault CLI or Tkinter GUI and smoke-check a change in the real app (not just tests). Use when asked to run, start, try, or demo youtube-vault, or to confirm a CLI/GUI change actually works.
---

# Launch youtube-vault

Always run through `uv` (the `python` on PATH may be the Windows Store stub) and always set
`PYTHONIOENCODING=utf-8`. Otherwise Rich crashes on Vietnamese text in a cp1252 console.

## 0. Prerequisites

```bash
uv sync                      # if .venv is missing or deps changed
ffmpeg -version              # needed only for real `video` / `audio` downloads
```

## 1. CLI smoke checks (cheapest first)

| Step | Command | Network | Touches user data |
|---|---|---|---|
| a | `PYTHONIOENCODING=utf-8 uv run youtube-vault --help` | no | no |
| b | `PYTHONIOENCODING=utf-8 uv run youtube-vault <command> --help` | no | no |
| c | `PYTHONIOENCODING=utf-8 uv run youtube-vault history list -n 5` | no | reads `~/.youtube-vault/history.db` |
| d | `PYTHONIOENCODING=utf-8 uv run youtube-vault info "https://www.youtube.com/watch?v=jNQXAC9IVRw"` | yes | no |
| e | real download, see below | yes | writes history and files |

Stop at the cheapest step that exercises the change. A change to option parsing only needs (a)/(b).
A change to metadata display needs (d).

**Real downloads (step e).** Use them only when the change is in the download path. Send the
output to the session scratchpad, never the user's `~/Downloads`:

```bash
PYTHONIOENCODING=utf-8 uv run youtube-vault audio "https://www.youtube.com/watch?v=jNQXAC9IVRw" -f m4a -o "<scratchpad>/yv-out"
```

`jNQXAC9IVRw` ("Me at the zoo", 19 s) is the canonical tiny test video. A successful download is
recorded in the user's real history DB, and a second run reports "Không có file mới nào được tải."
because of that. Add `--force` to re-download. Don't run `history clear` or `history remove`
unless asked.

## 2. GUI

The GUI blocks until its window closes, so start it in the background:

```bash
PYTHONIOENCODING=utf-8 uv run youtube-vault gui      # run_in_background: true
```

Or, when the `idea` MCP server is connected, run the IntelliJ config `youtube-vault GUI` with
`mcp__idea__execute_run_configuration` and `waitForExit: false`.

You can't see the window. Ask the user to check the specific thing you changed (name the tab,
button or column), then stop the background process. A crash at startup shows up as a traceback
in the background task output, so read it before asking.

For logic changes, prefer a headless check: the queue and the pure helpers in `gui.py`
(`parse_urls`, `format_row`, `item_percent`) can be exercised from `uv run python -c ...` or a test.

## 3. Report

Say exactly what you ran and what you observed (exit code, key output lines). If a step needed the
network or ffmpeg and that wasn't available, say it was skipped. Don't claim it passed.
