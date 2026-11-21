"""Ghi âm âm thanh đang phát trên máy (vd. trình duyệt đang mở video/livestream).

Dùng "loopback" của loa (WASAPI trên Windows, monitor của PulseAudio trên Linux)
qua thư viện ``soundcard``, rồi đẩy dữ liệu PCM thô vào ffmpeg để mã hóa sang
mp3/m4a/opus/wav. Ghi lại TOÀN BỘ âm thanh của máy, không riêng một tab.

Module này thuộc phần lõi: không import typer/rich/tkinter. ``soundcard`` được
import lười (chỉ khi thật sự ghi) để các lệnh khác và test không cần nó.
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
    """Máy không ghi được âm thanh hệ thống (thiếu thư viện, hệ điều hành không hỗ trợ, không có loa)."""


class RecordingFailedError(YoutubeVaultError):
    """ffmpeg hoặc thiết bị âm thanh báo lỗi trong lúc ghi."""


class AudioSource(Protocol):
    """Nguồn âm thanh: mỗi lần trả về một khối ``float32`` dạng (số_frame, số_kênh)."""

    def record(self, numframes: int) -> Any: ...


# Hàm mở nguồn âm thanh: nhận tên loa (None = loa mặc định), trả về context manager -> AudioSource.
SourceFactory = Callable[[str | None], Any]


# --------------------------------------------------------------------------- #
# soundcard
# --------------------------------------------------------------------------- #
def _soundcard() -> Any:
    try:
        import soundcard  # noqa: PLC0415 - import lười có chủ đích
    except Exception as exc:  # ImportError, hoặc lỗi khởi tạo thư viện âm thanh của hệ điều hành
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
    """Tên các loa có thể ghi lại (loa mặc định đứng đầu)."""
    sc = _soundcard()
    try:
        default = sc.default_speaker().name
        names = [s.name for s in sc.all_speakers()]
    except Exception as exc:
        raise RecorderUnavailableError(f"Không đọc được danh sách loa: {exc}") from exc
    return [default] + [n for n in names if n != default]


def _init_thread_com() -> None:
    """soundcard trên Windows dùng COM; khởi tạo COM cho luồng hiện tại cho chắc."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.ole32.CoInitializeEx(None, 0)  # type: ignore[attr-defined]
        except Exception:
            pass


def _soundcard_source(device: str | None) -> Any:
    """Mở loopback của loa ``device`` (None = loa mặc định) bằng soundcard."""
    sc = _soundcard()
    try:
        speaker = sc.default_speaker() if device is None else sc.get_speaker(device)
        mic = sc.get_microphone(str(speaker.name), include_loopback=True)
        return mic.recorder(samplerate=RECORD_SAMPLE_RATE, channels=RECORD_CHANNELS)
    except Exception as exc:
        name = device or "loa mặc định"
        raise RecorderUnavailableError(f"Không ghi được từ '{name}': {exc}") from exc


# --------------------------------------------------------------------------- #
# Hàm thuần
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
    """Lệnh ffmpeg đọc PCM float32 thô từ stdin và ghi ra ``out`` theo định dạng/bitrate."""
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
    """Tên file theo thời điểm bắt đầu ghi; thêm `` (2)``, `` (3)``… nếu đã tồn tại."""
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
    """Ghi âm thanh đang phát ra loa vào một file, chạy ở luồng nền.

    Dùng: ``r = Recorder(...); r.start(); ...; path = r.stop()``.
    Giao diện chỉ đọc ``elapsed``/``level``/``error`` (an toàn giữa các luồng);
    Recorder không bao giờ gọi ngược vào giao diện.
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
        self.level = 0.0  # độ to khối gần nhất, 0..1
        self.error: Exception | None = None
        self.path: Path | None = None

    @property
    def elapsed(self) -> float:
        """Số giây âm thanh đã ghi."""
        return self._frames / RECORD_SAMPLE_RATE

    @property
    def is_recording(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> Path:
        """Bắt đầu ghi; trả về đường dẫn file sẽ được tạo."""
        if self._thread is not None:
            raise RuntimeError("Recorder đã được bắt đầu")
        downloader._require_ffmpeg()
        if self._source_factory is _soundcard_source:
            _soundcard()  # báo lỗi thiếu thư viện ngay, không đợi tới luồng nền
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
                # soundcard cảnh báo "data discontinuity" khi máy bận; không ảnh hưởng file.
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
        """Dừng ghi, chờ ffmpeg ghi xong file; trả về đường dẫn file."""
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
