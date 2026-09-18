"""Юнит-тесты логики без ffmpeg: пути, битрейт, время, размеры, сбор файлов."""

import pytest

from converter.util import (
    ConverterError,
    collect_videos,
    human_size,
    parse_time,
    unique_path,
    validate_bitrate,
)


class TestValidateBitrate:
    def test_valid(self):
        assert validate_bitrate(192) == 192
        assert validate_bitrate("320") == 320
        assert validate_bitrate("128k") == 128

    def test_invalid_number(self):
        with pytest.raises(ConverterError):
            validate_bitrate(123)

    def test_garbage(self):
        with pytest.raises(ConverterError):
            validate_bitrate("высокое")


class TestUniquePath:
    def test_free_name_unchanged(self, tmp_path):
        target = tmp_path / "out.mp3"
        assert unique_path(target) == target

    def test_taken_gets_copy_number(self, tmp_path):
        target = tmp_path / "out.mp3"
        target.write_bytes(b"x")
        assert unique_path(target) == tmp_path / "out (1).mp3"

    def test_chain_of_copies(self, tmp_path):
        for name in ("out.mp3", "out (1).mp3", "out (2).mp3"):
            (tmp_path / name).write_bytes(b"x")
        assert unique_path(tmp_path / "out.mp3") == tmp_path / "out (3).mp3"


class TestParseTime:
    @pytest.mark.parametrize("value,expected", [
        (90, 90.0),
        (90.5, 90.5),
        ("90", 90.0),
        ("90.5", 90.5),
        ("90,5", 90.5),       # запятая как десятичный разделитель
        ("1:30", 90.0),
        ("0:05", 5.0),
        ("1:02:03", 3723.0),
        ("0:00:10.25", 10.25),
    ])
    def test_valid(self, value, expected):
        assert parse_time(value) == expected

    @pytest.mark.parametrize("value", ["", "abc", "-5", "-1:00", "1:2:3:4", "1:xx", True])
    def test_invalid(self, value):
        with pytest.raises(ConverterError):
            parse_time(value)

    def test_minutes_must_be_below_60(self):
        with pytest.raises(ConverterError):
            parse_time("75:00")  # часы не могут быть >= 60 в этой нотации


class TestHumanSize:
    @pytest.mark.parametrize("size,expected", [
        (0, "0 Б"),
        (512, "512 Б"),
        (2048, "2.0 КБ"),
        (2_421_451, "2.3 МБ"),
        (5 * 1024**3, "5.0 ГБ"),
    ])
    def test_format(self, size, expected):
        assert human_size(size) == expected


class TestCollectVideos:
    def test_files_and_dirs_mixed(self, tmp_path):
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        other = tmp_path / "b.txt"
        other.write_bytes(b"x")

        result = collect_videos([video, other, tmp_path])
        assert result == [video.resolve(), other.resolve()]

    def test_folder_filters_by_extension(self, tmp_path):
        for name in ("one.mp4", "two.MP4", "three.mkv", "skip.txt", "note.md"):
            (tmp_path / name).write_bytes(b"x")

        result = collect_videos([tmp_path])
        names = [p.name for p in result]
        assert names == ["one.mp4", "three.mkv", "two.MP4"]  # сортировка + регистр

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.mp4").write_bytes(b"x")
        (tmp_path / "top.mp4").write_bytes(b"x")

        flat = collect_videos([tmp_path])
        deep = collect_videos([tmp_path], recursive=True)
        assert len(flat) == 1
        assert len(deep) == 2

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(ConverterError):
            collect_videos([tmp_path / "нет такого"])


def test_human_time():
    from converter.util import human_time

    assert human_time(0) == "0:00"
    assert human_time(45) == "0:45"
    assert human_time(65) == "1:05"
    assert human_time(3665) == "1:01:05"
    assert human_time(-5) == "0:00"
    assert human_time(2.6) == "0:03"
