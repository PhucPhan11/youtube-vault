---
name: add-feature
description: End-to-end checklist for adding or changing a youtube-vault feature across config, core (downloader/history/download_queue), CLI, GUI, tests and the English README. Use when implementing a new option, command, download behaviour, or GUI capability.
---

# Add a feature to youtube-vault

Work through the layers in order. Skip a layer only when the feature truly doesn't touch it, and
say so in your summary. Read `CLAUDE.md` first if it isn't in context.

## 1. Config: `src/youtube_vault/config.py`
- New presets, defaults, limits or delays go here as typed module constants (`NAME: type = value`).
- Never read user-data paths (e.g. `HISTORY_DB`) at import time elsewhere. Tests monkeypatch them.

## 2. Core: `downloader.py` / `history.py` / `download_queue.py`
- No imports of `cli`, `gui`, `typer`, `rich` or `tkinter`.
- yt-dlp options are assembled in `downloader._base_opts` / `download_video` / `download_audio`.
  Thread new options through as keyword arguments with defaults from `config`.
- Signal failures with a `YoutubeVaultError` subclass. Don't let raw yt-dlp exceptions escape `_run`.
- If the feature affects queue items: add fields to `DownloadItem`, keep state changes under
  `self._lock`, and call `self._notify(item)` after each change. `on_update` runs on worker threads.
- If it affects history: change the schema in `_SCHEMA` (`history.py`) in a backward-compatible
  way. Existing users already have a `history.db`.

## 3. CLI: `src/youtube_vault/cli.py`
- Use the `Annotated[..., typer.Option("--long", "-s", help="<Vietnamese>")]` style. Use `Enum`s
  for fixed choices (see `Quality`, `AudioFormat`).
- Downloads go through `_run_download(lambda cb: downloader.<fn>(..., progress_callback=cb))` so
  progress and error rendering stay consistent.
- Output goes to `console` / `err_console` (Rich). Errors end with `raise typer.Exit(1)`.

## 4. GUI: `src/youtube_vault/gui.py`
- Add widgets in the matching `_build_*` method. Read their values in `add_from_input`.
- UI updates from download activity go only through `App._poll`. Never touch widgets from a queue
  callback.
- Put formatting logic in a pure module-level function (like `format_row`) so it can be tested
  without Tk.

## 5. Tests: `tests/`
- Core: `fake_ydl` (assert on `fake_ydl.instances[-1].opts`), `ffmpeg_present`/`ffmpeg_missing`,
  `history`.
- CLI: `CliRunner().invoke(app, [...])`, monkeypatching `downloader.*`.
- Queue: monkeypatch `downloader.download_video/download_audio` and use `wait_for(...)`.
- GUI: test the pure helpers.
- Cover the happy path, the main error path, and any skip/force or pause/cancel interaction.
- No network, no real ffmpeg, no real `~/.youtube-vault`.

## 6. Docs: `README.md` (English)
- Add a usage example and update the options table and/or the GUI section. Quote GUI labels in
  Vietnamese with an English gloss. Comments and docstrings in code are English; UI strings stay
  Vietnamese (see CLAUDE.md "Language rule"). Update the `Project structure`
  tree if you add a module.

## 7. Verify
```bash
uv run pytest
```
Then use `/launch-app` for a real-app smoke check of the touched front-end(s). The Stop hook will
re-run pytest. Fix failures, don't bypass them.
