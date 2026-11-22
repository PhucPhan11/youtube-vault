# Graph Report - youtube-vault  (2026-11-22)

## Corpus Check
- 24 files · ~25,593 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 4 file(s) not represented in the graph (top: (none) 1, .stackdump 1, .lock 1)

## Summary
- 732 nodes · 1732 edges · 33 communities (25 shown, 8 thin omitted)
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 316 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `376999a7`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Audio Recorder
- CLI Commands & Options
- SQLite Download History
- Project Docs & Design Rules
- Queue Tests & Row Formatting
- Stop Hook & Stdlib Imports
- GUI App Tabs
- Video Download Tests
- DownloadQueue Core
- GUI Live-Widget Tests
- CLI Tests
- Resolution Picker (GUI)
- Audio Download & Rename
- StripAudioSourceMarker
- FakeYoutubeDL
- yt-dlp Opts & Progress Hook
- ProgressEvent Flow
- Format Info & Resolutions
- Info Fetch & Silent Logger
- Audio CLI Option Tests
- Login Cookies
- Quality Normalization
- Media Info Display
- GUI Record & Kind Handlers
- GUI Recording Controls
- Fake Recorder (CLI tests)
- Friendly Error Hints
- GUI Input & Bitrate
- Claude Settings
- MP3 Button
- fake_recorder
- Bracketed ID Escaping
- Package Metadata

## God Nodes (most connected - your core abstractions)
1. `App` - 78 edges
2. `DownloadQueue` - 48 edges
3. `Cookies` - 36 edges
4. `download_video()` - 36 edges
5. `ItemStatus` - 35 edges
6. `History` - 31 edges
7. `download_audio()` - 29 edges
8. `ProgressEvent` - 27 edges
9. `make_queue()` - 26 edges
10. `DownloadItem` - 24 edges

## Surprising Connections (you probably didn't know these)
- `test_progress_event_percent_unknown_total()` --uses--> `ProgressEvent`  [INFERRED]
  tests/test_downloader.py → src/youtube_vault/downloader.py
- `test_resolution_label()` --uses--> `ResolutionOption`  [INFERRED]
  tests/test_downloader.py → src/youtube_vault/downloader.py
- `test_no_audio_error_is_not_retried_by_queue()` --uses--> `ItemStatus`  [INFERRED]
  tests/test_downloader.py → src/youtube_vault/download_queue.py
- `_item()` --uses--> `DownloadItem`  [INFERRED]
  tests/test_gui.py → src/youtube_vault/download_queue.py
- `make_queue()` --uses--> `DownloadQueue`  [INFERRED]
  tests/test_download_queue.py → src/youtube_vault/download_queue.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **add-feature layer checklist enforcing project rules** — _claude_skills_add_feature_skill_layered_feature_workflow, claude_layering_rule, claude_offline_testing_rules, claude_language_rule, claude_stop_hook [INFERRED 0.85]
- **Download pipeline mechanisms** — claude_download_flow, claude_history_skip_mechanism, claude_pause_cancel_mechanism, claude_resolution_choice, claude_filename_templates, claude_silent_logger_console_output [INFERRED 0.85]
- **Login cookie support across sites** — claude_sites_and_cookies, readme_facebook_downloads, readme_other_sites, readme_cookies_security_note [INFERRED 0.85]

## Communities (33 total, 8 thin omitted)

### Community 0 - "Audio Recorder"
Cohesion: 0.05
Nodes (30): record(), AudioSource, encoder_command(), _init_thread_com(), list_output_devices(), output_path(), Recorder, RecorderUnavailableError (+22 more)

### Community 1 - "CLI Commands & Options"
Cohesion: 0.06
Nodes (24): audio(), AudioFormat, _choose_quality(), _exit_ffmpeg_missing(), _fail(), _format_duration(), _format_size(), gui() (+16 more)

### Community 2 - "SQLite Download History"
Cohesion: 0.10
Nodes (18): History, HistoryArchive, HistoryRecord, HistoryRecorder, make_archive_id(), test_audio_not_skipped_when_only_video_in_history(), test_facebook_history_key(), hist() (+10 more)

### Community 3 - "Project Docs & Design Rules"
Cohesion: 0.06
Nodes (32): add-feature skill (end-to-end feature checklist), _run_download progress/error rendering pattern, Background GUI launch and user-verified check, Canonical tiny test video jNQXAC9IVRw (Me at the zoo), launch-app skill (real-app smoke check), Download flow (_run, _base_opts, exception mapping), YoutubeVaultError hierarchy and front-end error handling, Filename templates (OUTPUT_TEMPLATE, AUDIO_SOURCE_TEMPLATE) (+24 more)

### Community 4 - "Queue Tests & Row Formatting"
Cohesion: 0.12
Nodes (38): ItemStatus, _format_eta(), format_row(), item_percent(), fake(), make_queue(), factory(), test_audio_options_passed_to_downloader() (+30 more)

### Community 5 - "Stop Hook & Stdlib Imports"
Cohesion: 0.05
Nodes (21): main(), main(), ffmpeg_install_instructions(), is_ffmpeg_available(), list_supported_sites(), _remove_audio_sources(), _require_ffmpeg(), _site_from_message() (+13 more)

### Community 7 - "Video Download Tests"
Cohesion: 0.10
Nodes (26): download_video(), test_already_downloaded_is_skipped(), test_cancel_during_download_raises(), test_cancel_event_already_set(), test_cancel_hook_installed_without_callback(), test_cookie_load_error_is_wrapped_with_cause(), test_download_error_is_wrapped(), test_download_video_accepts_any_height() (+18 more)

### Community 8 - "DownloadQueue Core"
Cohesion: 0.09
Nodes (10): _clamp_workers(), DownloadItem, DownloadQueue, callback(), DownloadCancelledError, FFmpegNotFoundError, NoAudioStreamError, YoutubeVaultError (+2 more)

### Community 9 - "GUI Live-Widget Tests"
Cohesion: 0.13
Nodes (18): FakeGuiRecorder, _media_1440_720(), _pump(), test_app_adds_facebook_url_with_cookies(), test_app_bitrate_disabled_for_wav(), test_app_cookie_modes(), test_app_default_device_is_none(), test_app_device_list_error_shown() (+10 more)

### Community 10 - "CLI Tests"
Cohesion: 0.10
Nodes (13): _capture(), _seed_history(), test_audio_cookies_from_browser(), test_both_cookie_sources_rejected(), test_default_cookie_file_is_used_and_announced(), test_history_clear_requires_confirmation(), test_history_list_and_search(), test_history_open_calls_open_folder() (+5 more)

### Community 11 - "Resolution Picker (GUI)"
Cohesion: 0.10
Nodes (9): ResolutionOption, work(), _format_size(), parse_urls(), quality_choices(), test_parse_urls(), test_parse_urls_empty(), test_quality_choices_fixed_list() (+1 more)

### Community 12 - "Audio Download & Rename"
Cohesion: 0.15
Nodes (13): download_audio(), _audio_source(), test_audio_passes_cookies_from_browser(), test_audio_without_sound_raises_no_audio_and_cleans_up(), test_download_audio_bitrate(), test_download_audio_embed_without_cover_for_other_formats(), test_download_audio_embeds_metadata_and_cover(), test_download_audio_no_embed() (+5 more)

### Community 13 - "StripAudioSourceMarker"
Cohesion: 0.33
Nodes (3): StripAudioSourceMarker, test_strip_audio_source_marker_ignores_other_files(), test_strip_audio_source_marker_renames()

### Community 15 - "yt-dlp Opts & Progress Hook"
Cohesion: 0.22
Nodes (9): _base_opts(), _iter_filepaths(), _make_hook(), hook(), _parse_format(), _parse_info(), _run(), _to_progress_event() (+1 more)

### Community 16 - "ProgressEvent Flow"
Cohesion: 0.16
Nodes (8): ProgressEvent, on_skip(), test_video_passes_options_and_reports_files(), fake_download(), test_video_reports_skipped(), fake_download(), FakeDownload, fake_download()

### Community 17 - "Format Info & Resolutions"
Cohesion: 0.27
Nodes (9): _audio_size_estimate(), FormatInfo, list_resolutions(), _nominal_height(), _media(), test_list_resolutions_empty_for_playlist_and_audio_only(), test_list_resolutions_groups_and_sorts(), test_list_resolutions_prefers_format_note() (+1 more)

### Community 18 - "Info Fetch & Silent Logger"
Cohesion: 0.16
Nodes (13): DownloadFailedError, get_info(), _SilentLogger, test_error_keeps_site_tag(), boom(), test_info_error_exits_cleanly(), boom(), test_cookie_load_error_in_get_info() (+5 more)

### Community 19 - "Audio CLI Option Tests"
Cohesion: 0.15
Nodes (7): test_audio_defaults_best_bitrate_and_embed(), test_audio_passes_bitrate_and_no_embed(), test_audio_passes_format(), test_force_flag_is_passed(), test_video_accepts_any_height(), fake_download(), test_video_choose_prompts_and_uses_selection()

### Community 20 - "Login Cookies"
Cohesion: 0.26
Nodes (8): Cookies, _cookie_file(), test_cookies_resolve_explicit(), test_cookies_resolve_none_without_default(), test_cookies_resolve_rejects_invalid(), test_cookies_resolve_uses_default_file_when_present(), test_get_info_passes_cookies(), test_video_passes_cookie_file()

### Community 21 - "Quality Normalization"
Cohesion: 0.18
Nodes (8): _validate_quality(), format_sort_for(), normalize_quality(), _select(), test_format_sort_returns_copy(), test_normalize_quality(), test_real_ytdlp_picks_requested_resolution(), test_real_ytdlp_prefers_mp4_at_same_resolution()

### Community 22 - "Media Info Display"
Cohesion: 0.20
Nodes (10): MediaInfo, fake(), _media_with_resolutions(), test_info_lists_resolutions(), test_info_playlist(), test_info_prints_title(), test_info_title_with_brackets(), test_video_choose_default_is_highest() (+2 more)

### Community 23 - "GUI Record & Kind Handlers"
Cohesion: 0.20
Nodes (3): work(), format_elapsed(), test_format_elapsed()

### Community 25 - "Fake Recorder (CLI tests)"
Cohesion: 0.22
Nodes (5): FakeRecorder, test_record_unavailable_exits_1(), broken(), test_record_without_ffmpeg_shows_instructions(), broken()

### Community 26 - "Friendly Error Hints"
Cohesion: 0.22
Nodes (6): _error_message(), friendly_error(), test_friendly_error_chrome_cookie_decrypt(), test_friendly_error_facebook_login(), test_friendly_error_leaves_other_messages(), test_friendly_error_login_hint_names_site()

### Community 28 - "Claude Settings"
Cohesion: 0.29
Nodes (6): hooks, PreToolUse, Stop, permissions, allow, $schema

## Knowledge Gaps
- **11 isolated node(s):** `youtube-vault`, `_run_download progress/error rendering pattern`, `Canonical tiny test video jNQXAC9IVRw (Me at the zoo)`, `History skip-already-downloaded via download_archive`, `IntelliJ IDEA MCP server and run configs` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 206 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `App` connect `GUI App Tabs` to `Audio Recorder`, `SQLite Download History`, `Queue Tests & Row Formatting`, `Stop Hook & Stdlib Imports`, `DownloadQueue Core`, `GUI Live-Widget Tests`, `Resolution Picker (GUI)`, `Login Cookies`, `Media Info Display`, `GUI Record & Kind Handlers`, `GUI Recording Controls`, `GUI Input & Bitrate`, `MP3 Button`?**
  _High betweenness centrality (0.155) - this node is a cross-community bridge._
- **Are the 23 inferred relationships involving `App` (e.g. with `DownloadItem` and `DownloadQueue`) actually correct?**
  _`App` has 23 INFERRED edges - model-reasoned connections that need verification._
- **What connects `youtube-vault`, `_run_download progress/error rendering pattern`, `Canonical tiny test video jNQXAC9IVRw (Me at the zoo)` to the rest of the system?**
  _11 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Audio Recorder` be split into smaller, more focused modules?**
  _Cohesion score 0.053208137715179966 - nodes in this community are weakly interconnected._
- **Why does `History` connect `SQLite Download History` to `CLI Commands & Options`, `Stop Hook & Stdlib Imports`, `GUI App Tabs`, `Video Download Tests`, `DownloadQueue Core`, `CLI Tests`, `Audio Download & Rename`, `yt-dlp Opts & Progress Hook`?**
  _High betweenness centrality (0.077) - this node is a cross-community bridge._
- **Are the 23 inferred relationships involving `DownloadQueue` (e.g. with `Cookies` and `DownloadCancelledError`) actually correct?**
  _`DownloadQueue` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Should `CLI Commands & Options` be split into smaller, more focused modules?**
  _Cohesion score 0.06299603174603174 - nodes in this community are weakly interconnected._