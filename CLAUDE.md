# youtube-vault

Download YouTube video/audio to disk. Thin wrapper around **yt-dlp** with two front-ends:
a **Typer + Rich CLI** and a **Tkinter desktop GUI** (multi-threaded queue, pause/resume/cancel,
auto-retry, SQLite download history). Python ≥ 3.11, managed with **uv**, built with hatchling,
`src/` layout. The README is the user manual (in Vietnamese).

## Language rule

These agent docs are English, but **the project itself is Vietnamese**: code comments, docstrings,
CLI `help=` text, GUI labels, error messages and `README.md` must stay in Vietnamese. Identifiers
stay English.

## Commands

```bash
uv sync                                   # create/refresh .venv (incl. dev group: pytest)
uv run pytest                             # full suite (~5 s, fully offline)
uv run pytest tests/test_download_queue.py -k pause -q
uv run youtube-vault --help               # commands: video, audio, record, info, history, gui
uv run youtube-vault record -t 3 -o <dir> # records real system audio (needs a sound device)
uv run youtube-vault info <url>           # metadata only, no ffmpeg needed (hits network)
uv run youtube-vault video <url> -q 720 -o <dir>
uv run youtube-vault audio <url> -f m4a -o <dir>
uv run youtube-vault history list -n 50 -s <text>
uv run youtube-vault gui                  # blocks until the window is closed
```

- **Windows console encoding:** Rich output contains Vietnamese characters. Without
  `PYTHONIOENCODING=utf-8` a cp1252 console crashes with `UnicodeEncodeError`. Always prefix
  manual CLI runs: `PYTHONIOENCODING=utf-8 uv run youtube-vault --help`.
- `python` on PATH may be the Windows Store stub; always go through `uv run`.
- `video`/`audio` need **ffmpeg** on PATH. Without it they print install instructions and exit 1
  (`FFmpegNotFoundError`), they do not crash.

## Architecture

```
src/youtube_vault/
  config.py          constants: output dir, filename template, quality presets, queue tunables
  downloader.py      yt-dlp wrapper: get_info, download_video, download_audio, ProgressEvent, errors
  history.py         SQLite History + yt-dlp glue (HistoryArchive, HistoryRecorder)
  download_queue.py  DownloadQueue: bounded worker threads, pause/resume/cancel, retries
  recorder.py        Recorder: system-audio loopback (soundcard) piped as f32le PCM into ffmpeg
  platform_utils.py  open_folder() per OS
  cli.py             Typer app (entry point `youtube-vault`)
  gui.py             Tkinter App (entry point `youtube-vault-gui`, also `youtube-vault gui`)
```

**Layering rule:** the core (`config`, `downloader`, `history`, `download_queue`, `recorder`,
`platform_utils`) never imports `cli`, `gui`, `typer`, `rich` or `tkinter`. Both front-ends depend
only on the core. `cli.gui` imports `gui` lazily so the CLI doesn't need Tk.

Key flows:
- **Download:** `downloader._run` builds yt-dlp opts (`_base_opts`), opens `yt_dlp.YoutubeDL`, and
  maps yt-dlp exceptions to our own. Returns the list of produced file paths.
- **History / skip-already-downloaded:** `HistoryArchive` is passed as yt-dlp's
  `download_archive` (membership check means "skip"; it must stay truthy, see `__bool__`).
  `HistoryRecorder` is an `after_move` postprocessor that writes the record. Video and audio are
  tracked separately (`kind`). `force=True` bypasses the archive.
- **Pause/cancel:** the progress hook from `_make_hook` raises yt-dlp `DownloadCancelled` when
  `cancel_event` is set, which surfaces as `DownloadCancelledError`. Pause keeps `.part` files so
  yt-dlp resumes; cancel removes them (`DownloadQueue._cleanup_partials`). The ffmpeg merge/convert
  step cannot be interrupted, so a stop takes effect after it.
- **Threading:** `DownloadQueue` runs one daemon thread per active item (≤ `max_workers`, 1–5),
  guards state with `_lock`, retries network failures with `RETRY_DELAYS` (never `FFmpegNotFoundError`),
  and calls `on_update(item)` **from worker threads**. The GUI only pushes item ids into a
  `SimpleQueue` there and renders in `App._poll` (Tk `after`, 100 ms). Other background work
  (e.g. "Xem độ phân giải") sends callables through `App._ui_calls`, which `_poll` also drains.
  **Never touch Tk widgets from a worker thread.**
- **Resolution choice:** `quality` is `"best"` or any height (`normalize_quality`). Selection uses
  yt-dlp `format_sort` (`res:N` = largest ≤ N, else smallest above; `res` is the short side, so
  Shorts/21:9 work), not `[height<=N]` filters. See `config.QUALITY_PRESETS` and
  `downloader.format_sort_for`. `list_resolutions(media)` gives the real per-video list.
- **Recording:** `recorder.Recorder.start()` opens `ffmpeg` (`encoder_command`) with stdin as a pipe
  and a `yv-recorder` thread that reads 100 ms loopback blocks via `soundcard` and writes them to it;
  `stop()` closes stdin and waits for ffmpeg. `soundcard` is imported lazily (`_soundcard()`), so
  nothing else needs it. The GUI "Ghi âm" tab polls `elapsed`/`level`/`error` in `_poll`; `stop()`
  runs off the Tk thread and reports back via `_ui_calls`. Recordings are not added to history.
  Tests inject `source_factory`/`popen` (`Recorder`) and `recorder_factory`/`list_devices` (`App`),
  so they never open a real sound device or ffmpeg.
- **Errors:** the core raises `YoutubeVaultError` subclasses (`FFmpegNotFoundError`,
  `DownloadFailedError`, `DownloadCancelledError`). The CLI turns them into Rich messages and
  `typer.Exit(1)` (`cli._run_download`). The GUI shows them per item.

## Testing rules

- Tests are **fully offline**. Never let a test reach YouTube or ffmpeg. Use the fixtures in
  `tests/conftest.py`:
  - `fake_ydl`: replaces `yt_dlp.YoutubeDL` with `FakeYoutubeDL`. Set `.result`, `.error` and
    `.hook_events`, and inspect `.instances[i].opts`. It simulates `download_archive` skipping,
    progress hooks and `after_move` postprocessors.
  - `ffmpeg_present` / `ffmpeg_missing`: patch `shutil.which`.
  - `history`: a `History` on a temp DB.
  - Autouse `_isolated_history_db` redirects `config.HISTORY_DB` to `tmp_path`, so tests never
    touch `~/.youtube-vault`. Keep it that way. Don't read `HISTORY_DB` at import time.
- CLI tests use `typer.testing.CliRunner` and monkeypatch `downloader.*` functions.
- Queue tests monkeypatch `downloader.download_video/download_audio` and poll with
  `wait_for(cond)` (`tests/test_download_queue.py`). Build queues with that file's `make_queue()`
  helper, which defaults `retry_delays=(0,)` so retries don't sleep.
- GUI tests cover pure helpers (`parse_urls`, `format_row`, `item_percent`, `quality_choices`),
  plus live-widget tests that use the `tk_root` fixture (it skips when Tk has no display) and
  `_pump(root, cond)` to drive the event loop until a condition holds.
- yt-dlp format selection is tested offline with the **real** `YoutubeDL.process_ie_result(...,
  download=False)` on synthetic format lists (`_select` in `tests/test_downloader.py`). Extend it
  whenever `VIDEO_FORMAT` or `QUALITY_PRESETS` change.
- Every behaviour change ships with a test. `uv run pytest` must be green before finishing (the
  Stop hook enforces it, see below).

## Conventions

- `from __future__ import annotations` at the top of every module. Full type hints, `@dataclass`
  for value objects, `str`-valued `Enum`s for choices.
- Tunables and presets live in `config.py`, not inline.
- New CLI options use the `Annotated[..., typer.Option(...)]` style already in `cli.py`.
- Every user-visible feature gets a section or row in `README.md` (Vietnamese).
- Commits so far are phase-sized: `Phase N: <summary>`.

## Environment & gotchas

- Real (non-test) runs write to the user's data: history in `~/.youtube-vault/history.db`,
  downloads in `~/Downloads/youtube-vault/`. For manual checks pass `-o <scratch dir>`. Never run
  `history clear` or `history remove` against the real DB unless asked.
- The IDE is IntelliJ IDEA (Python plugin). The `idea` MCP server (`.mcp.json`) exposes
  inspections, symbol search and run configs. The local run configs in `.idea/runConfigurations/`
  are `youtube-vault GUI`, `youtube-vault CLI` (prompts for args) and `pytest`.

## Agent harness

- `.claude/settings.json`: allowlist for safe commands (pytest, `--help`, `info`, `history list`,
  read-only git and `idea` tools), plus the Stop hook.
- **Stop hook** `.claude/hooks/run_tests.py`: when `src/`, `tests/`, `pyproject.toml` or
  `uv.lock` have uncommitted changes, it runs pytest before you finish. On failure it blocks with
  the tail of the output, so fix the tests and don't work around the hook.
- Skills: `/launch-app` (run the CLI or GUI and smoke-check it), `/add-feature` (end-to-end
  checklist: config → core → CLI → GUI → tests → README).
