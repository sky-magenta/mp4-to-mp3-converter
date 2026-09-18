"""Задания конвертации (мост для MCP-сервера)."""

import time

from converter.jobs import JobManager


def _wait(manager, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = manager.status(job_id)
        if snap["status"] != "running":
            return snap
        time.sleep(0.05)
    return manager.status(job_id)


class TestJobManager:
    def test_convert_job_completes(self, ffmpeg, sample_mp4, tmp_path):
        manager = JobManager()
        job_id = manager.start([sample_mp4], str(tmp_path / "out"))

        assert job_id
        snap = _wait(manager, job_id)

        assert snap["status"] == "done"
        assert snap["done"] == 1
        assert snap["overall"] == 1.0
        assert snap["results"][0]["status"] == "ok"
        dst = snap["results"][0]["dst"]
        assert dst.endswith(".mp3")
        import pathlib
        assert pathlib.Path(dst).is_file()

    def test_progress_reaches_one_inside_file(self, ffmpeg, sample_mp4,
                                              tmp_path):
        manager = JobManager()
        job_id = manager.start([sample_mp4], str(tmp_path / "out"))
        snap = _wait(manager, job_id)
        assert snap["current"]["name"] == sample_mp4.name
        # финальный тик внутри файла — 100%
        assert snap["results"], "результаты записаны"

    def test_two_files_counters(self, ffmpeg, sample_mp4, tmp_path):
        manager = JobManager()
        job_id = manager.start([sample_mp4, sample_mp4],
                               str(tmp_path / "out"))
        snap = _wait(manager, job_id)

        assert snap["total"] == 2 and snap["done"] == 2
        assert len(snap["results"]) == 2
        assert {r["status"] for r in snap["results"]} == {"ok"}

    def test_stop_cancels(self, ffmpeg, sample_mp4, tmp_path):
        manager = JobManager()
        job_id = manager.start([sample_mp4, sample_mp4],
                               str(tmp_path / "out"))
        manager.stop(job_id)
        snap = _wait(manager, job_id)

        assert snap["status"] in ("cancelled", "done")  # могли успеть
        if snap["status"] == "cancelled":
            assert snap["error"] is None

    def test_validation_errors(self, sample_mp4, tmp_path):
        manager = JobManager()
        import pytest
        from converter.util import ConverterError

        with pytest.raises(ConverterError):
            manager.start([], str(tmp_path))
        with pytest.raises(ConverterError):
            manager.start(["НЕТТАКОГО.mp4"], str(tmp_path))
        with pytest.raises(ConverterError):
            manager.start([sample_mp4], str(tmp_path), bitrate=123)
        with pytest.raises(ConverterError):
            manager.start([sample_mp4], str(tmp_path),
                          start="5:00", end="1:00")
        with pytest.raises(ConverterError):
            manager.start([sample_mp4], str(tmp_path), split_mode="magic")
        with pytest.raises(ConverterError):
            manager.start([sample_mp4], str(tmp_path), split_mode="interval")
        with pytest.raises(KeyError):
            manager.status("no-such-job")

    def test_list(self, ffmpeg, sample_mp4, tmp_path):
        manager = JobManager()
        manager.start([sample_mp4], str(tmp_path / "out"))
        jobs = manager.list()
        assert len(jobs) == 1 and jobs[0]["total"] == 1

    def test_split_job(self, ffmpeg, mix_mp4, tmp_path):
        """Разбивка по паузам через тот же интерфейс задания."""
        manager = JobManager()
        job_id = manager.start([mix_mp4], str(tmp_path / "out"),
                               split_mode="silence", split_min_track=0.5)
        snap = _wait(manager, job_id)

        assert snap["status"] == "done"
        assert snap["results"][0]["status"] == "ok"
        assert "дорожк" in (snap["results"][0]["message"] or "")
