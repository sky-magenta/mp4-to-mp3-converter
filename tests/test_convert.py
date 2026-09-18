"""Интеграционные тесты: реальное кодирование через ffmpeg."""

import re
import subprocess
import threading

import pytest

from conftest import ffmpeg_info
from converter.core import (
    CANCELLED,
    OK,
    SKIPPED,
    convert_batch,
    convert_file,
    probe_duration,
)

_TITLE_LINE_RE = re.compile(r"^\s+title\s*:\s*(.*)$", re.MULTILINE)


def _title_line(ffmpeg, path) -> str | None:
    """Значение тега title из метаданных файла (или None)."""
    match = _TITLE_LINE_RE.search(ffmpeg_info(ffmpeg, path))
    return match.group(1).strip() if match else None


def is_mp3(path) -> bool:
    """Проверяем сигнатуру: ID3v2-тег или синхрослово MPEG-кадра."""
    with open(path, "rb") as fh:
        head = fh.read(2)
    return head[:3] == b"ID" or (head[0] == 0xFF and head[1] & 0xE0 == 0xE0)


class TestSingleFile:
    def test_basic_conversion(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg)

        assert result.status == OK, result.message
        assert result.dst == tmp_path / "sample.mp3"
        assert result.dst.is_file()
        assert result.dst.stat().st_size > 1000
        assert is_mp3(result.dst)

    def test_duration_preserved(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg)

        assert result.status == OK
        assert probe_duration(ffmpeg, result.dst) == pytest.approx(2.0, abs=0.4)

    def test_progress_callback(self, ffmpeg, sample_mp4, tmp_path):
        seen = []
        result = convert_file(
            sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg, progress=seen.append,
        )

        assert result.status == OK
        assert seen and seen[-1] == pytest.approx(1.0)
        assert all(0.0 <= v <= 1.0 for v in seen)

    def test_no_audio_stream_message(self, ffmpeg, noaudio_mp4, tmp_path):
        result = convert_file(noaudio_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg)

        assert result.status == "failed"
        assert "звуковой дорожки" in result.message

    def test_missing_file(self, ffmpeg, tmp_path):
        result = convert_file(tmp_path / "ghost.mp4", ffmpeg=ffmpeg)

        assert result.status == "failed"
        assert result.message == "файл не найден"

    def test_bitrate_affects_size(self, ffmpeg, sample_mp4, tmp_path):
        small = convert_file(sample_mp4, dst=tmp_path / "small.mp3",
                             bitrate=96, ffmpeg=ffmpeg)
        big = convert_file(sample_mp4, dst=tmp_path / "big.mp3",
                           bitrate=320, ffmpeg=ffmpeg)
        assert small.status == OK and big.status == OK
        assert big.dst.stat().st_size > small.dst.stat().st_size * 2


class TestOverwritePolicies:
    def test_skip_existing(self, ffmpeg, sample_mp4, tmp_path):
        dst = tmp_path / "sample.mp3"
        dst.write_bytes(b"existing")
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              overwrite="skip", ffmpeg=ffmpeg)

        assert result.status == SKIPPED
        assert dst.read_bytes() == b"existing"  # не тронули

    def test_rename_creates_copy(self, ffmpeg, sample_mp4, tmp_path):
        dst = tmp_path / "sample.mp3"
        dst.write_bytes(b"existing")
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              overwrite="rename", ffmpeg=ffmpeg)

        assert result.status == OK
        assert result.dst == tmp_path / "sample (1).mp3"
        assert dst.read_bytes() == b"existing"

    def test_overwrite_replaces(self, ffmpeg, sample_mp4, tmp_path):
        dst = tmp_path / "sample.mp3"
        dst.write_bytes(b"old")
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              overwrite="overwrite", ffmpeg=ffmpeg)

        assert result.status == OK
        assert dst.stat().st_size > 1000


class TestBatch:
    def test_two_files_and_callback(self, ffmpeg, sample_mp4, tmp_path):
        second = tmp_path / "copy.mp4"
        second.write_bytes(sample_mp4.read_bytes())

        events = []
        summary = convert_batch(
            [sample_mp4, second], dst_dir=tmp_path / "out",
            ffmpeg=ffmpeg, on_result=lambda r, i, n: events.append((i, n, r.status)),
        )

        assert len(summary.ok) == 2
        assert len(summary.failed) == 0
        assert [e[0] for e in events] == [1, 2]
        assert all(e[1] == 2 for e in events)
        assert (tmp_path / "out" / "sample.mp3").is_file()

    def test_parallel(self, ffmpeg, sample_mp4, tmp_path):
        files = []
        for i in range(4):
            copy = tmp_path / f"v{i}.mp4"
            copy.write_bytes(sample_mp4.read_bytes())
            files.append(copy)

        summary = convert_batch(files, dst_dir=tmp_path / "out",
                                ffmpeg=ffmpeg, parallel=2)

        assert len(summary.ok) == 4
        assert summary.skipped == [] and summary.failed == []


class TestCli:
    def test_cli_end_to_end(self, ffmpeg, sample_mp4, tmp_path, capsys):
        from converter.cli import main

        code = main([str(sample_mp4), "-o", str(tmp_path / "cli_out"), "-b", "128"])
        out = capsys.readouterr().out

        assert code == 0
        assert "Готово: 1" in out
        assert (tmp_path / "cli_out" / "sample.mp3").is_file()

    def test_cli_no_files_found(self, ffmpeg, tmp_path, capsys):
        from converter.cli import main

        code = main([str(tmp_path)])
        assert code == 0
        assert "не найдено" in capsys.readouterr().out.lower()


class TestFfmpegHelpers:
    def test_probe_duration(self, ffmpeg, sample_mp4):
        assert probe_duration(ffmpeg, sample_mp4) == pytest.approx(2.0, abs=0.3)

    def test_ffmpeg_runs(self, ffmpeg):
        proc = subprocess.run([str(ffmpeg), "-version"], capture_output=True)
        assert proc.returncode == 0


class TestFragment:
    def test_trim_seconds(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              start=0.5, end=1.5, ffmpeg=ffmpeg)

        assert result.status == OK
        assert probe_duration(ffmpeg, result.dst) == pytest.approx(1.0, abs=0.3)

    def test_trim_time_strings(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              start="0:00.5", end="0:01", ffmpeg=ffmpeg)

        assert result.status == OK
        assert probe_duration(ffmpeg, result.dst) == pytest.approx(0.5, abs=0.3)

    def test_end_before_start_fails(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              start="0:02", end="0:01", ffmpeg=ffmpeg)

        assert result.status == "failed"
        assert "позже начала" in result.message

    def test_garbage_time_fails(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              start="завтра", ffmpeg=ffmpeg)

        assert result.status == "failed"
        assert "время" in result.message.lower()

    def test_fragment_beyond_eof_fails(self, ffmpeg, sample_mp4, tmp_path):
        """Фрагмент целиком за пределами файла не должен выдавать пустое «готово»."""
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              start=100, end=110, ffmpeg=ffmpeg)

        assert result.status == "failed"
        assert "за пределами" in result.message
        assert not (tmp_path / "sample.mp3").exists()

    def test_fragment_tail_clipped_to_eof(self, ffmpeg, sample_mp4, tmp_path):
        """Начало внутри файла, конец за EOF — обрезается по концу."""
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              start="0:01", end="0:40", ffmpeg=ffmpeg)

        assert result.status == OK
        assert probe_duration(ffmpeg, result.dst) == pytest.approx(1.0, abs=0.4)

    def test_string_paths_accepted(self, ffmpeg, sample_mp4, tmp_path):
        """Публичный API принимает пути строками, а не только Path."""
        result = convert_file(str(sample_mp4), dst_dir=str(tmp_path),
                              ffmpeg=ffmpeg)

        assert result.status == OK
        assert result.dst == tmp_path / "sample.mp3"

    def test_batch_string_dst_dir(self, ffmpeg, sample_mp4, tmp_path):
        from converter.core import convert_batch

        summary = convert_batch([str(sample_mp4)],
                                dst_dir=str(tmp_path / "batch"),
                                ffmpeg=ffmpeg)
        assert len(summary.ok) == 1
        assert (tmp_path / "batch" / "sample.mp3").is_file()

    def test_worker_exception_becomes_failed(self, ffmpeg, sample_mp4,
                                             tmp_path, monkeypatch):
        """Исключение внутри воркера не убивает партию, а даёт «failed»."""
        import converter.core as core

        real = core.convert_file
        state = {"boom": True}

        def flaky(src, *args, **kwargs):
            if state["boom"]:
                state["boom"] = False
                raise RuntimeError("бум в воркере")
            return real(src, *args, **kwargs)

        monkeypatch.setattr(core, "convert_file", flaky)
        summary = core.convert_batch(
            [sample_mp4, sample_mp4], dst_dir=tmp_path / "out",
            ffmpeg=ffmpeg, overwrite="overwrite")
        monkeypatch.undo()

        assert len(summary.results) == 2
        messages = [r.message for r in summary.results]
        assert any("внутренняя ошибка" in m for m in messages)
        assert len(summary.ok) == 1


class TestAudioOptions:
    def test_normalize_ok(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              normalize=True, ffmpeg=ffmpeg)

        assert result.status == OK, result.message
        assert probe_duration(ffmpeg, result.dst) == pytest.approx(2.0, abs=0.5)

    def test_mono_single_channel(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              mono=True, ffmpeg=ffmpeg)

        assert result.status == OK
        assert "mono" in ffmpeg_info(ffmpeg, result.dst)

    def test_title_tag_written(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg)

        assert result.status == OK
        line = _title_line(ffmpeg, result.dst)
        assert line is not None and "sample" in line

    def test_no_title_tag(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              title_tag=False, ffmpeg=ffmpeg)

        assert result.status == OK
        assert _title_line(ffmpeg, result.dst) is None

    def test_result_has_size(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg)

        assert result.status == OK
        assert result.size_bytes == result.dst.stat().st_size > 1000


class TestCancellation:
    def test_stop_before_start(self, ffmpeg, sample_mp4, tmp_path):
        stop = threading.Event()
        stop.set()
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              ffmpeg=ffmpeg, stop=stop)

        assert result.status == CANCELLED
        assert not (tmp_path / "sample.mp3").exists()

    def test_cancel_mid_batch(self, ffmpeg, sample_mp4, tmp_path):
        files = []
        for i in range(3):
            copy = tmp_path / f"v{i}.mp4"
            copy.write_bytes(sample_mp4.read_bytes())
            files.append(copy)

        stop = threading.Event()

        def on_result(result, index, total):
            if index >= 1:  # после первого файла — стоп
                stop.set()

        summary = convert_batch(files, dst_dir=tmp_path / "out",
                                ffmpeg=ffmpeg, on_result=on_result, stop=stop)

        assert len(summary.ok) == 1
        assert len(summary.cancelled) == 2
        assert summary.cancelled[0].message == "отменено"
        assert summary.elapsed_sec >= 0


class TestBatchSummaryFields:
    def test_totals(self, ffmpeg, sample_mp4, tmp_path):
        summary = convert_batch([sample_mp4], dst_dir=tmp_path, ffmpeg=ffmpeg)

        assert summary.total_size == summary.ok[0].size_bytes > 0
        assert summary.elapsed_sec > 0


class TestCliNewFlags:
    def test_cli_fragment(self, ffmpeg, sample_mp4, tmp_path, capsys):
        from converter.cli import main

        code = main([str(sample_mp4), "-o", str(tmp_path / "frag"),
                     "--start", "0:00.5", "--end", "0:01.5",
                     "--normalize", "--mono"])
        out = capsys.readouterr().out

        assert code == 0
        assert "фрагмент" in out
        mp3 = tmp_path / "frag" / "sample.mp3"
        assert mp3.is_file()
        assert probe_duration(ffmpeg, mp3) == pytest.approx(1.0, abs=0.3)

    def test_cli_report_has_size(self, ffmpeg, sample_mp4, tmp_path, capsys):
        from converter.cli import main

        code = main([str(sample_mp4), "-o", str(tmp_path / "sizes")])
        out = capsys.readouterr().out

        assert code == 0
        assert "МБ" in out or "КБ" in out


class TestLiveProgress:
    def test_convert_file_progress_ticks(self, ffmpeg, sample_mp4, tmp_path):
        ticks: list[float] = []
        result = convert_file(sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg,
                              progress=ticks.append)

        assert result.status == OK
        assert ticks, "прогресс не вызывался"
        assert ticks == sorted(ticks)          # не убывает
        assert ticks[-1] == pytest.approx(1.0)  # финальный тик — 100%

    def test_batch_on_progress_per_file(self, ffmpeg, sample_mp4, tmp_path):
        events: list[tuple[int, str, float]] = []
        summary = convert_batch(
            [sample_mp4, sample_mp4], dst_dir=tmp_path / "out",
            ffmpeg=ffmpeg, on_progress=lambda i, name, f: events.append((i, name, f)),
        )

        assert summary.ok and len(summary.ok) == 2
        firsts = {i: next(f for ii, _n, f in events if ii == i)
                  for i in (0, 1)}
        lasts = {}
        for i, _n, f in events:
            lasts[i] = f
        for i in (0, 1):
            assert firsts[i] == 0.0            # стартовый тик до запуска ffmpeg
            assert lasts[i] == pytest.approx(1.0)
        assert events[0][1] == sample_mp4.name
