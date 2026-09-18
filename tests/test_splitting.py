"""Тесты разбивки на дорожки и улучшений звука (нормализация, шум, дорожки)."""

import pytest

from conftest import ffmpeg_info
from converter.core import OK, convert_file, measure_loudness, probe_duration
from converter.splitting import (
    aggregate_split_results,
    detect_silences,
    interval_segments,
    segments_from_silences,
    split_to_tracks,
)
from converter.util import ConverterError, ru_plural


class TestRuPlural:
    @pytest.mark.parametrize("n,expected", [
        (1, "дорожка"), (2, "дорожки"), (5, "дорожек"),
        (11, "дорожек"), (21, "дорожка"), (22, "дорожки"), (25, "дорожек"),
        (100, "дорожек"), (101, "дорожка"), (111, "дорожек"),
    ])
    def test_forms(self, n, expected):
        assert ru_plural(n, "дорожка", "дорожки", "дорожек") == expected


class TestSegmentsPure:
    def test_interval(self):
        assert interval_segments(10, 3) == [(0, 3), (3, 6), (6, 9), (9, 10)]

    def test_interval_exact_multiple(self):
        assert interval_segments(9, 3) == [(0, 3), (3, 6), (6, 9)]

    def test_interval_bad_step(self):
        with pytest.raises(ConverterError):
            interval_segments(10, 0)

    def test_silence_midpoints(self):
        # тон 2с, пауза 1.5с, тон 2с → режем по центрам пауз
        segments = segments_from_silences([(2.0, 3.5), (5.5, 7.0)], 9.0,
                                          min_track=0.5)
        assert segments == [(0.0, 2.75), (2.75, 6.25), (6.25, 9.0)]

    def test_leading_trailing_silence_trimmed(self):
        segments = segments_from_silences([(0.0, 1.0), (4.0, 5.0), (8.0, 9.0)], 9.0,
                                          min_track=0.5)
        assert segments[0][0] == 1.0          # начало после ведущей тишины
        assert segments[-1][1] == 8.0         # конец перед хвостовой

    def test_short_segments_merged_not_dropped(self):
        # паузы часто, min_track большой: всё сливается в одну дорожку,
        # но покрытие остаётся полным — содержание не теряется
        segments = segments_from_silences([(2.0, 3.5), (4.0, 5.5)], 9.0,
                                          min_track=8.0)
        assert segments == [(0.0, 9.0)]

    def test_merge_keeps_short_pieces_with_neighbours(self):
        segments = segments_from_silences([(2, 3), (4, 5), (10, 11)], 14.0,
                                          min_track=4.0)
        # сырые куски 2.5 / 2 / 6 / 3.5 с — короткие подклеены к соседним
        assert segments == [(0.0, 4.5), (4.5, 14.0)]

    def test_short_first_track_merged_into_second(self):
        # первый кусок 3.5 с при min_track=10 — вливается в следующий
        segments = segments_from_silences([(3, 4)], 30.0, min_track=10.0)
        assert segments == [(0.0, 30.0)]

    def test_all_tracks_reach_min_track(self):
        segments = segments_from_silences(
            [(2, 3), (4.5, 5.5), (30, 31), (60, 61)], 100.0, min_track=15.0)
        assert all(b - a >= 15.0 for a, b in segments)
        assert sum(b - a for a, b in segments) == pytest.approx(100.0)


class TestDetectSilences:
    def test_mix_has_two_pauses(self, ffmpeg, mix_mp4):
        silences = detect_silences(ffmpeg, mix_mp4)
        assert len(silences) == 2
        starts = [s for s, _ in silences]
        assert starts[0] == pytest.approx(2.0, abs=0.2)
        assert starts[1] == pytest.approx(5.5, abs=0.2)
        for _, end in silences:
            assert end is not None


class TestSplitToTracks:
    def test_split_by_silence(self, ffmpeg, mix_mp4, tmp_path):
        results = split_to_tracks(mix_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg,
                                  min_track=1.0)

        assert all(r.status == OK for r in results), [r.message for r in results]
        assert len(results) == 3
        names = sorted(p.name for p in tmp_path.glob("mix *.mp3"))
        assert names == ["mix 01.mp3", "mix 02.mp3", "mix 03.mp3"]
        # режем по серединам пауз: дорожка = тон + по полпаузы с краёв,
        # средняя — тон и по полпаузы с двух сторон
        durations = [probe_duration(ffmpeg, r.dst) for r in results]
        assert durations == pytest.approx([2.75, 3.5, 2.75], abs=0.4)

    def test_split_by_interval(self, ffmpeg, sample_mp4, tmp_path):
        results = split_to_tracks(sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg,
                                  mode="interval", interval=1.0)

        assert len(results) == 2
        assert all(r.status == OK for r in results)
        durations = sorted(
            probe_duration(ffmpeg, r.dst) for r in results)
        assert durations == pytest.approx([1.0, 1.0], abs=0.3)

    def test_split_respects_overwrite_skip(self, ffmpeg, mix_mp4, tmp_path):
        first = split_to_tracks(mix_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg,
                                min_track=1.0)
        assert all(r.status == OK for r in first)

        second = split_to_tracks(mix_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg,
                                 min_track=1.0, overwrite="skip")
        assert all(r.status == "skipped" for r in second)
        aggregate = aggregate_split_results(second, mix_mp4)
        assert aggregate.status == "skipped"

    def test_split_aggregate_message(self, ffmpeg, mix_mp4, tmp_path):
        results = split_to_tracks(mix_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg,
                                  min_track=1.0)
        aggregate = aggregate_split_results(results, mix_mp4)

        assert aggregate.status == OK
        assert aggregate.message == "3 дорожки"
        assert aggregate.size_bytes == sum(r.size_bytes for r in results)

    def test_no_silences_message(self, ffmpeg, sample_mp4, tmp_path):
        results = split_to_tracks(sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg,
                                  min_track=1.0)

        assert len(results) == 1
        assert results[0].status == "failed"
        assert "паузы не найдены" in results[0].message

    def test_shorter_than_interval(self, ffmpeg, sample_mp4, tmp_path):
        results = split_to_tracks(sample_mp4, dst_dir=tmp_path, ffmpeg=ffmpeg,
                                  mode="interval", interval="10:00")

        assert results[0].status == "failed"
        assert "короче интервала" in results[0].message


class TestTwoPassLoudnorm:
    def test_measure_returns_stats(self, ffmpeg, sample_mp4):
        measured = measure_loudness(ffmpeg, sample_mp4)

        assert measured is not None
        for key in ("input_i", "input_tp", "input_lra", "input_thresh",
                    "target_offset"):
            assert key in measured

    def test_normalized_to_target_loudness(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              normalize=True, ffmpeg=ffmpeg)
        assert result.status == OK, result.message

        after = measure_loudness(ffmpeg, result.dst)
        assert after is not None
        # EBU R128: цель -16 LUFS, допускаем люфт измерения
        assert float(after["input_i"]) == pytest.approx(-16.0, abs=1.5)

    def test_sample_rate_back_to_44100(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              normalize=True, ffmpeg=ffmpeg)

        info = ffmpeg_info(ffmpeg, result.dst)
        assert "44100 Hz" in info


class TestDenoise:
    def test_converts_ok(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              denoise=True, ffmpeg=ffmpeg)

        assert result.status == OK, result.message
        assert probe_duration(ffmpeg, result.dst) == pytest.approx(2.0, abs=0.5)

    def test_denoise_plus_normalize(self, ffmpeg, sample_mp4, tmp_path):
        result = convert_file(sample_mp4, dst_dir=tmp_path,
                              denoise=True, normalize=True, mono=True,
                              ffmpeg=ffmpeg)

        assert result.status == OK, result.message


class TestAudioStreamSelection:
    def test_first_stream(self, ffmpeg, two_streams_mkv, tmp_path):
        result = convert_file(two_streams_mkv, dst_dir=tmp_path,
                              audio_stream=0, ffmpeg=ffmpeg)

        assert result.status == OK
        assert probe_duration(ffmpeg, result.dst) == pytest.approx(3.0, abs=0.3)

    def test_second_stream(self, ffmpeg, two_streams_mkv, tmp_path):
        result = convert_file(two_streams_mkv, dst_dir=tmp_path,
                              audio_stream=1, ffmpeg=ffmpeg)

        assert result.status == OK
        assert probe_duration(ffmpeg, result.dst) == pytest.approx(5.0, abs=0.3)


class TestCliSplit:
    def test_cli_split_silence(self, ffmpeg, mix_mp4, tmp_path, capsys):
        from converter.cli import main

        code = main([str(mix_mp4), "-o", str(tmp_path / "tracks"),
                     "--split-silence", "--min-track", "1"])
        out = capsys.readouterr().out

        assert code == 0
        assert "разбивка по паузам" in out
        assert "3 дорожки" in out
        assert len(list((tmp_path / "tracks").glob("mix *.mp3"))) == 3

    def test_cli_split_every(self, ffmpeg, sample_mp4, tmp_path, capsys):
        from converter.cli import main

        code = main([str(sample_mp4), "-o", str(tmp_path / "chunks"),
                     "--split-every", "1"])
        out = capsys.readouterr().out

        assert code == 0
        assert "разбивка по 1" in out
        assert len(list((tmp_path / "chunks").glob("sample *.mp3"))) == 2

    def test_cli_both_split_modes_rejected(self, ffmpeg, mix_mp4, tmp_path):
        from converter.cli import main

        with pytest.raises(SystemExit):
            main([str(mix_mp4), "--split-silence", "--split-every", "10:00"])
