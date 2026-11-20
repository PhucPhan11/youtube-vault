from __future__ import annotations

import pytest

from youtube_vault.history import (
    History,
    HistoryArchive,
    HistoryRecorder,
    make_archive_id,
)


@pytest.fixture
def hist(tmp_path):
    return History(tmp_path / "sub" / "h.db")


def test_creates_db_file(tmp_path):
    History(tmp_path / "a" / "h.db")
    assert (tmp_path / "a" / "h.db").exists()


def test_add_get_contains(hist):
    rec = hist.add("youtube abc", "video", title="T", url="u", filepath="f.mp4")
    assert rec.id > 0
    assert rec.title == "T"
    assert hist.contains("youtube abc", "video")
    assert not hist.contains("youtube abc", "audio")
    assert hist.get("youtube xyz", "video") is None
    assert hist.get_by_id(rec.id) == rec


def test_add_is_upsert(hist):
    first = hist.add("youtube abc", "video", title="Old")
    second = hist.add("youtube abc", "video", title="New", filepath="new.mp4")
    assert first.id == second.id
    assert second.title == "New"
    assert len(hist.list()) == 1


def test_video_and_audio_are_separate(hist):
    hist.add("youtube abc", "video")
    hist.add("youtube abc", "audio")
    assert len(hist.list()) == 2


def test_list_search_and_limit(hist):
    for i in range(5):
        hist.add(f"youtube {i}", "video", title=f"Clip {i}")
    hist.add("youtube zoo", "video", title="Me at the zoo")

    assert len(hist.list(limit=3)) == 3
    assert [r.title for r in hist.list(search="zoo")] == ["Me at the zoo"]
    # mới nhất trước
    assert hist.list()[0].title == "Me at the zoo"


def test_remove_and_clear(hist):
    a = hist.add("youtube a", "video")
    hist.add("youtube b", "video")
    assert hist.remove(a.id) is True
    assert hist.remove(a.id) is False
    assert hist.clear() == 1
    assert hist.list() == []


def test_make_archive_id():
    assert make_archive_id({"extractor_key": "Youtube", "id": "abc"}) == "youtube abc"
    assert make_archive_id({"id": "abc"}) is None
    assert make_archive_id({"extractor_key": "Youtube"}) is None


def test_archive_contains_triggers_on_skip(hist):
    hist.add("youtube abc", "video", title="Done")
    skipped = []
    archive = HistoryArchive(hist, "video", on_skip=skipped.append)

    assert bool(archive) is True
    assert "youtube abc" in archive
    assert "youtube other" not in archive
    assert 123 not in archive
    assert [r.title for r in skipped] == ["Done"]
    archive.add("youtube new")  # không ghi gì
    assert not hist.contains("youtube new", "video")


def test_archive_respects_kind(hist):
    hist.add("youtube abc", "video")
    assert "youtube abc" not in HistoryArchive(hist, "audio")


def test_recorder_writes_record(hist):
    pp = HistoryRecorder(hist, "audio")
    info = {
        "id": "abc",
        "extractor_key": "Youtube",
        "title": "Song",
        "webpage_url": "https://youtu.be/abc",
        "filepath": "C:/out/Song [abc].mp3",
    }
    files, returned = pp.run(info)
    assert files == []
    assert returned is info
    rec = hist.get("youtube abc", "audio")
    assert rec is not None
    assert rec.title == "Song"
    assert rec.filepath == "C:/out/Song [abc].mp3"
    assert rec.url == "https://youtu.be/abc"
