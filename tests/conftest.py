"""Общие фикстуры: рабочий ffmpeg и сгенерированные тестовые видео."""

import subprocess
from pathlib import Path

import pytest

from converter.ffmpeg_setup import ensure_ffmpeg, find_ffmpeg


def ffmpeg_info(ffmpeg: Path, path: Path) -> str:
    """Вывод `ffmpeg -i` — оттуда берём каналы, герцы и метаданные."""
    proc = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(path)],
        capture_output=True, timeout=60, check=False,
    )
    return proc.stderr.decode("utf-8", "replace")


@pytest.fixture(scope="session")
def ffmpeg() -> Path:
    found = find_ffmpeg()
    return found if found else ensure_ffmpeg()


def _run(ffmpeg: Path, args: list[str]) -> None:
    proc = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def _make_video(ffmpeg: Path, dst: Path, audio: bool) -> Path:
    if audio:
        # Синус 440 Гц + цветные кадры: 2 секунды, h264 + aac.
        for video_codec in ("libx264", "mpeg4"):
            try:
                _run(ffmpeg, [
                    "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", video_codec, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(dst),
                ])
                return dst
            except AssertionError:
                continue
        raise RuntimeError("не удалось собрать тестовое видео с кодеком")
    _run(ffmpeg, [
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(dst),
    ])
    return dst


@pytest.fixture(scope="session")
def sample_mp4(ffmpeg, tmp_path_factory) -> Path:
    """2-секундное mp4 со звуком (440 Гц)."""
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    return _make_video(ffmpeg, path, audio=True)


@pytest.fixture(scope="session")
def noaudio_mp4(ffmpeg, tmp_path_factory) -> Path:
    """mp4 без звуковой дорожки — для проверки понятной ошибки."""
    path = tmp_path_factory.mktemp("media") / "noaudio.mp4"
    return _make_video(ffmpeg, path, audio=False)


@pytest.fixture(scope="session")
def mix_mp4(ffmpeg, tmp_path_factory) -> Path:
    """«Микс»: тон 2с — тишина 1.5с — тон 2с — тишина 1.5с — тон 2с."""
    path = tmp_path_factory.mktemp("media") / "mix.mp4"
    _run(ffmpeg, [
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "anullsrc=duration=1.5:channel_layout=mono",
        "-f", "lavfi", "-i", "sine=frequency=523:duration=2",
        "-f", "lavfi", "-i", "anullsrc=duration=1.5:channel_layout=mono",
        "-f", "lavfi", "-i", "sine=frequency=659:duration=2",
        "-filter_complex",
        "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[out]",
        "-map", "[out]", "-c:a", "aac", str(path),
    ])
    return path


@pytest.fixture(scope="session")
def two_streams_mkv(ffmpeg, tmp_path_factory) -> Path:
    """mkv с двумя звуковыми дорожками разной длины: 3 с и 5 с."""
    path = tmp_path_factory.mktemp("media") / "twostreams.mkv"
    _run(ffmpeg, [
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=5",
        "-map", "0:a", "-map", "1:a", "-c:a", "aac", str(path),
    ])
    return path
