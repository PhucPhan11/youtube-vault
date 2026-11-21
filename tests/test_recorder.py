"""Test ghi âm bằng nguồn âm thanh và ffmpeg giả (không đụng loa thật, không chạy ffmpeg)."""

from __future__ import annotations

import io
import sys
import time
from datetime import datetime

import numpy as np
import pytest

from youtube_vault import recorder
from youtube_vault.config import RECORD_CHANNELS, RECORD_SAMPLE_RATE
from youtube_vault.downloader import FFmpegNotFoundError
from youtube_vault.recorder import (
    Recorder,
    RecorderUnavailableError,
    RecordingFailedError,
    encoder_command,
    output_path,
)

BLOCK = int(RECORD_SAMPLE_RATE * recorder.RECORD_BLOCK_SECONDS)


# ------------------------------ hàm thuần ---------------------------------- #
def _codec_args(cmd: list[str]) -> list[str]:
    return cmd[cmd.index("pipe:0") + 1 : -1]


def test_encoder_command_reads_raw_pcm_from_stdin(tmp_path):
    out = tmp_path / "a.mp3"
    cmd = encoder_command(out, "mp3", "best")
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-f") + 1] == "f32le"
    assert cmd[cmd.index("-ar") + 1] == str(RECORD_SAMPLE_RATE)
    assert cmd[cmd.index("-ac") + 1] == str(RECORD_CHANNELS)
    assert cmd[-1] == str(out)


@pytest.mark.parametrize(
    ("fmt", "quality", "expected"),
    [
        ("mp3", "best", ["-c:a", "libmp3lame", "-q:a", "0"]),
        ("mp3", "192", ["-c:a", "libmp3lame", "-b:a", "192k"]),
        ("m4a", "best", ["-c:a", "aac", "-b:a", "256k"]),
        ("m4a", "128", ["-c:a", "aac", "-b:a", "128k"]),
        ("opus", "best", ["-c:a", "libopus", "-b:a", "192k"]),
        ("wav", "320", ["-c:a", "pcm_s16le"]),
    ],
)
def test_encoder_command_codec_and_bitrate(tmp_path, fmt, quality, expected):
    assert _codec_args(encoder_command(tmp_path / f"a.{fmt}", fmt, quality)) == expected


@pytest.mark.parametrize(("fmt", "quality"), [("flac", "best"), ("mp3", "999")])
def test_encoder_command_rejects_invalid(tmp_path, fmt, quality):
    with pytest.raises(ValueError):
        encoder_command(tmp_path / "a", fmt, quality)


def test_output_path_uses_timestamp_and_avoids_collisions(tmp_path):
    now = datetime(2026, 11, 21, 9, 5, 7)
    first = output_path(tmp_path, "mp3", now)
    assert first == tmp_path / "Ghi âm 2026-11-21 09-05-07.mp3"
    first.write_bytes(b"")
    second = output_path(tmp_path, "mp3", now)
    assert second == tmp_path / "Ghi âm 2026-11-21 09-05-07 (2).mp3"
    second.write_bytes(b"")
    assert output_path(tmp_path, "mp3", now).name.endswith("(3).mp3")


# ------------------------------- Recorder ---------------------------------- #
class FakeSource:
    """Nguồn âm thanh giả: trả về ``blocks`` khối rồi chờ tới khi bị dừng."""

    def __init__(self, blocks: int = 3, amplitude: float = 0.5, error: Exception | None = None):
        self.blocks = blocks
        self.amplitude = amplitude
        self.error = error
        self.served = 0
        self.device: str | None = "chưa mở"

    def __call__(self, device):
        self.device = device
        return self

    def __enter__(self):
        if self.error is not None:
            raise self.error
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        if self.served >= self.blocks:
            time.sleep(0.005)
            return np.zeros((0, RECORD_CHANNELS), dtype="float32")
        self.served += 1
        return np.full((numframes, RECORD_CHANNELS), self.amplitude, dtype="float32")


class FakeProc:
    def __init__(self, cmd, returncode=0, stderr=b""):
        self.cmd = cmd
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None  # giữ lại dữ liệu để kiểm tra
        self.returncode = None
        self._rc = returncode
        self._stderr = stderr

    def communicate(self, timeout=None):
        self.returncode = self._rc
        return b"", self._stderr

    def kill(self):
        pass


def _fake_popen(procs, **proc_kw):
    def popen(cmd, **kwargs):
        proc = FakeProc(cmd, **proc_kw)
        procs.append(proc)
        return proc

    return popen


def _wait(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("Hết thời gian chờ")


def test_recorder_streams_pcm_to_ffmpeg(ffmpeg_present, tmp_path):
    source, procs = FakeSource(blocks=3), []
    rec = Recorder(tmp_path / "out", "mp3", "192", "Loa A",
                   source_factory=source, popen=_fake_popen(procs))
    path = rec.start()
    assert path.parent == tmp_path / "out" and path.suffix == ".mp3"
    assert rec.is_recording
    _wait(lambda: source.served == 3)
    _wait(lambda: rec.elapsed == pytest.approx(3 * BLOCK / RECORD_SAMPLE_RATE))
    assert rec.level == pytest.approx(0.5)

    assert rec.stop() == path
    assert not rec.is_recording
    assert source.device == "Loa A"
    proc = procs[0]
    assert proc.cmd == encoder_command(path, "mp3", "192")
    assert len(proc.stdin.getvalue()) == 3 * BLOCK * RECORD_CHANNELS * 4  # float32


def test_recorder_ffmpeg_failure_raises(ffmpeg_present, tmp_path):
    procs: list[FakeProc] = []
    rec = Recorder(tmp_path, source_factory=FakeSource(blocks=1),
                   popen=_fake_popen(procs, returncode=1, stderr=b"Unknown encoder\n"))
    rec.start()
    with pytest.raises(RecordingFailedError, match="Unknown encoder"):
        rec.stop()


def test_recorder_device_error_is_reported(ffmpeg_present, tmp_path):
    err = RecorderUnavailableError("Không ghi được từ 'Loa X'")
    rec = Recorder(tmp_path, source_factory=FakeSource(error=err), popen=_fake_popen([]))
    rec.start()
    _wait(lambda: rec.error is not None)
    with pytest.raises(RecorderUnavailableError, match="Loa X"):
        rec.stop()


def test_recorder_requires_ffmpeg(ffmpeg_missing, tmp_path):
    procs: list[FakeProc] = []
    rec = Recorder(tmp_path, source_factory=FakeSource(), popen=_fake_popen(procs))
    with pytest.raises(FFmpegNotFoundError):
        rec.start()
    assert procs == []


def test_recorder_rejects_invalid_options(tmp_path):
    with pytest.raises(ValueError):
        Recorder(tmp_path, fmt="flac")


def test_stop_before_start_raises(tmp_path):
    with pytest.raises(RuntimeError):
        Recorder(tmp_path).stop()


def test_missing_soundcard_is_unavailable(monkeypatch, ffmpeg_present, tmp_path):
    monkeypatch.setitem(sys.modules, "soundcard", None)  # import soundcard -> ImportError
    with pytest.raises(RecorderUnavailableError):
        recorder.list_output_devices()
    procs: list[FakeProc] = []
    with pytest.raises(RecorderUnavailableError):
        Recorder(tmp_path, popen=_fake_popen(procs)).start()
    assert procs == []  # không mở ffmpeg khi thiếu thư viện
