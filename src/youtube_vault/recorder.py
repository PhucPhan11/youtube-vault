"""Record the audio playing on the computer (e.g. a browser playing a video/livestream).

Uses the speaker "loopback" (WASAPI on Windows, the PulseAudio monitor on Linux)
through the ``soundcard`` library, and pipes raw PCM into ffmpeg to encode it as
mp3/m4a/opus/wav. Records ALL of the computer's sound, not a single tab.

This module is part of the core: no typer/rich/tkinter imports. ``soundcard`` is
imported lazily (only when actually recording) so other commands and tests don't need it.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import warnings
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from . import downloader
from .config import (
    AUDIO_FORMATS,
    AUDIO_QUALITIES,
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_OUTPUT_DIR,
    RECORD_BEST_BITRATE,
    RECORD_BLOCK_SECONDS,
    RECORD_CHANNELS,
    RECORD_FILENAME,
    RECORD_SAMPLE_RATE,
)
from .downloader import YoutubeVaultError

__all__ = [
    "Recorder",
    "RecorderUnavailableError",
    "RecordingFailedError",
    "encoder_command",
    "list_output_devices",
    "output_path",
]


class RecorderUnavailableError(YoutubeVaultError):
    """System audio can't be recorded (library missing, unsupported OS, no speaker)."""


class RecordingFailedError(YoutubeVaultError):
    """ffmpeg or the audio device failed while recording."""


class AudioSource(Protocol):
    """Audio source: each call returns a ``float32`` block shaped (frames, channels)."""

    def record(self, numframes: int) -> Any: ...


# Opens an audio source: takes a speaker name (None = default), returns a context manager -> AudioSource.
SourceFactory = Callable[[str | None], Any]


# --------------------------------------------------------------------------- #
# soundcard
# --------------------------------------------------------------------------- #
def _soundcard() -> Any:
    try:
        import soundcard  # noqa: PLC0415 - deliberately lazy import
    except Exception as exc:  # ImportError, or the OS audio library failed to initialise
        hint = ""
        if sys.platform == "darwin":
            hint = (
                "\nmacOS không hỗ trợ ghi âm thanh hệ thống sẵn; hãy cài thiết bị ảo như "
                "BlackHole (https://github.com/ExistentialAudio/BlackHole)."
            )
        raise RecorderUnavailableError(
            f"Không dùng được thư viện ghi âm 'soundcard': {exc}{hint}"
        ) from exc
    return soundcard


def list_output_devices() -> list[str]:
    """Names of the speakers that can be recorded (default speaker first)."""
    sc = _soundcard()
    try:
        default = sc.default_speaker().name
        names = [s.name for s in sc.all_speakers()]
    except Exception as exc:
        raise RecorderUnavailableError(f"Không đọc được danh sách loa: {exc}") from exc
    return [default] + [n for n in names if n != default]


def _init_thread_com() -> None:
    """soundcard uses COM on Windows; initialise COM for the current thread to be safe."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.ole32.CoInitializeEx(None, 0)  # type: ignore[attr-defined]
        except Exception:
            pass


def _soundcard_source(device: str | None) -> Any:
    """Open the loopback of speaker ``device`` (None = default speaker) with soundcard."""
    sc = _soundcard()
    try:
        speaker = sc.default_speaker() if device is None else sc.get_speaker(device)
        mic = sc.get_microphone(str(speaker.name), include_loopback=True)
        return mic.recorder(samplerate=RECORD_SAMPLE_RATE, channels=RECORD_CHANNELS)
    except Exception as exc:
        name = device or "loa mặc định"
        raise RecorderUnavailableError(f"Không ghi được từ '{name}': {exc}") from exc


# --------------------------------------------------------------------------- #
# Pure functions
# --------------------------------------------------------------------------- #
def _validate(fmt: str, audio_quality: str) -> None:
    if fmt not in AUDIO_FORMATS:
        raise ValueError(
            f"Định dạng audio không hợp lệ: {fmt!r}. Chọn một trong: {', '.join(AUDIO_FORMATS)}"
        )
    if audio_quality not in AUDIO_QUALITIES:
        raise ValueError(
            f"Bitrate không hợp lệ: {audio_quality!r}. "
            f"Chọn một trong: {', '.join(AUDIO_QUALITIES)}"
        )


def encoder_command(out: Path, fmt: str, audio_quality: str) -> list[str]:
    """ffmpeg command that reads raw float32 PCM from stdin and writes ``out`` in the given format/bitrate."""
    _validate(fmt, audio_quality)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "f32le", "-ar", str(RECORD_SAMPLE_RATE), "-ac", str(RECORD_CHANNELS),
        "-i", "pipe:0",
    ]
    if fmt == "wav":
        cmd += ["-c:a", "pcm_s16le"]
    elif fmt == "mp3":
        cmd += ["-c:a", "libmp3lame"]
        cmd += ["-q:a", "0"] if audio_quality == "best" else ["-b:a", f"{audio_quality}k"]
    else:
        codec = {"m4a": "aac", "opus": "libopus"}[fmt]
        kbps = RECORD_BEST_BITRATE[fmt] if audio_quality == "best" else audio_quality
        cmd += ["-c:a", codec, "-b:a", f"{kbps}k"]
    cmd.append(str(out))
    return cmd


def output_path(out_dir: Path | str, fmt: str, now: datetime | None = None) -> Path:
    """File name from the start time; adds `` (2)``, `` (3)``… if it already exists."""
    out = Path(out_dir).expanduser()
    stem = (now or datetime.now()).strftime(RECORD_FILENAME)
    path = out / f"{stem}.{fmt}"
    n = 2
    while path.exists():
        path = out / f"{stem} ({n}).{fmt}"
        n += 1
    return path


# --------------------------------------------------------------------------- #
# Recorder
# --------------------------------------------------------------------------- #
class Recorder:
    """Records the sound going to the speakers into a file, on a background thread.

    Usage: ``r = Recorder(...); r.start(); ...; path = r.stop()``.
    A UI only reads ``elapsed``/``level``/``error`` (safe across threads);
    the Recorder never calls back into the UI.
    """

    def __init__(
        self,
        output_dir: Path | str = DEFAULT_OUTPUT_DIR,
        fmt: str = DEFAULT_AUDIO_FORMAT,
        audio_quality: str = DEFAULT_AUDIO_QUALITY,
        device: str | None = None,
        *,
        source_factory: SourceFactory | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        _validate(fmt, audio_quality)
        self.output_dir = Path(output_dir).expanduser()
        self.fmt = fmt
        self.audio_quality = audio_quality
        self.device = device
        self._source_factory = source_factory or _soundcard_source
        self._popen = popen
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: Any = None
        self._frames = 0
        self.level = 0.0  # peak level of the latest block, 0..1
        self.error: Exception | None = None
        self.path: Path | None = None

    @property
    def elapsed(self) -> float:
        """Seconds of audio recorded so far."""
        return self._frames / RECORD_SAMPLE_RATE

    @property
    def is_recording(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> Path:
        """Start recording; return the path of the file that will be created."""
        if self._thread is not None:
            raise RuntimeError("Recorder đã được bắt đầu")
        downloader._require_ffmpeg()
        if self._source_factory is _soundcard_source:
            _soundcard()  # report a missing library now, not later on the worker thread
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = output_path(self.output_dir, self.fmt)
        self._proc = self._popen(
            encoder_command(self.path, self.fmt, self.audio_quality),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._thread = threading.Thread(target=self._worker, daemon=True, name="yv-recorder")
        self._thread.start()
        return self.path

    def _blocks(self, source: AudioSource) -> Iterator[Any]:
        block = max(1, int(RECORD_SAMPLE_RATE * RECORD_BLOCK_SECONDS))
        while not self._stop_event.is_set():
            yield source.record(numframes=block)

    def _worker(self) -> None:
        _init_thread_com()
        try:
            with warnings.catch_warnings():
                # soundcard warns about "data discontinuity" when the machine is busy; the file is fine.
                warnings.simplefilter("ignore")
                with self._source_factory(self.device) as source:
                    for data in self._blocks(source):
                        if len(data) == 0:
                            continue
                        self._proc.stdin.write(data.astype("float32", copy=False).tobytes())
                        self._frames += len(data)
                        self.level = min(1.0, float(abs(data).max()))
        except Exception as exc:
            self.error = exc
            self._stop_event.set()

    def stop(self, timeout: float = 15.0) -> Path:
        """Stop recording, wait for ffmpeg to finish the file; return its path."""
        if self._thread is None or self.path is None:
            raise RuntimeError("Recorder chưa được bắt đầu")
        self._stop_event.set()
        self._thread.join(timeout)
        self.level = 0.0
        proc = self._proc
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            _, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RecordingFailedError("ffmpeg không kết thúc kịp khi lưu file ghi âm") from None
        if isinstance(self.error, YoutubeVaultError):
            raise self.error
        if self.error is not None:
            raise RecordingFailedError(f"Lỗi khi ghi âm: {self.error}") from self.error
        if proc.returncode != 0:
            msg = (stderr or b"").decode("utf-8", "replace").strip().splitlines()[-5:]
            raise RecordingFailedError("ffmpeg báo lỗi khi mã hóa:\n" + "\n".join(msg))
        return self.path
