"""Поиск, проверка и скачивание ffmpeg.

Порядок поиска:
1. Переменная окружения ``FFMPEG_PATH`` (полный путь до ffmpeg.exe).
2. Локальная папка ``bin`` рядом с программой (сюда же ставит кнопка
   «Установить ffmpeg» — переносимая копия не оставляет следов в системе).
3. Папка в профиле пользователя (осталась от старых версий).
4. ``ffmpeg`` в PATH.

Если нигде нет — кнопка «Установить ffmpeg» (или ``--setup-ffmpeg``)
скачивает официальную сборку для Windows (~80–170 МБ) в папку ``bin``
рядом с программой. Если рядом писать нельзя (Program Files, диск
только для чтения) — в профиль пользователя как запасной вариант.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .util import ConverterError

# Официальные сборки для Windows (перечислены в порядке приоритета).
FFMPEG_URLS_WINDOWS = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
)

EXE_SUFFIX = ".exe" if os.name == "nt" else ""


class FFmpegNotFoundError(ConverterError):
    """ffmpeg не найден, а скачать его не получилось (или запрещено)."""


def app_dir() -> Path:
    """Папка, где лежит программа (или exe после сборки PyInstaller)."""
    if getattr(sys, "frozen", False):  # запущен собранный .exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def local_bin_dir() -> Path:
    """Папка bin рядом с программой — сюда ставится ffmpeg (портативно)."""
    return app_dir() / "bin"


def cache_bin_dir() -> Path:
    """Папка в профиле пользователя (запасная, если рядом с exe писать нельзя)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "mp4-to-mp3-converter" / "bin"


def _is_writable(directory: Path) -> bool:
    """Можно ли создавать файлы в папке (Program Files, CD — нельзя)."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def install_dir() -> Path:
    """Куда ставить ffmpeg: рядом с программой, а если нельзя — в профиль."""
    if getattr(sys, "frozen", False) and _is_writable(app_dir()):
        return local_bin_dir()
    # из исходников не портимся: не сыпем файлы в репозиторий
    return cache_bin_dir()


def data_dir() -> Path:
    """Папка настроек/лога exe: рядом с программой, иначе профиль."""
    return install_dir().parent if getattr(sys, "frozen", False) \
        else cache_bin_dir().parent


def _ffmpeg_name() -> str:
    return "ffmpeg" + EXE_SUFFIX


def candidate_paths() -> list[Path]:
    """Все места, где ищем ffmpeg, в порядке приоритета."""
    candidates = []
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(local_bin_dir() / _ffmpeg_name())
    candidates.append(cache_bin_dir() / _ffmpeg_name())
    which = shutil.which("ffmpeg")
    if which:
        candidates.append(Path(which))
    return candidates


def _works(ffmpeg_path: Path) -> bool:
    """Проверяем, что бинарник реально запускается."""
    try:
        proc = subprocess.run(
            [str(ffmpeg_path), "-version"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and b"ffmpeg" in proc.stdout.lower()


def find_ffmpeg() -> Path | None:
    """Ищет ffmpeg в известных местах. Возвращает путь или None."""
    for candidate in candidate_paths():
        if candidate.is_file() and _works(candidate):
            return candidate
    return None


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def ensure_ffmpeg(progress=None, allow_download: bool = True) -> Path:
    """Возвращает путь до рабочего ffmpeg, при необходимости скачивая его.

    ``progress(received, total)`` вызывается во время загрузки; ``total``
    может быть None, если сервер не отдал размер.
    """
    found = find_ffmpeg()
    if found:
        return found
    if not allow_download:
        raise FFmpegNotFoundError(_not_found_message(download_hint=False))
    if os.name != "nt":
        # Для macOS/Linux автоматическое скачивание не делаем —
        # там ffmpeg ставится пакетным менеджером.
        raise FFmpegNotFoundError(_not_found_message(download_hint=True))

    target_dir = install_dir()
    for url in FFMPEG_URLS_WINDOWS:
        try:
            return _download_and_extract(url, target_dir, progress)
        except Exception as exc:  # пробуем следующее зеркало
            print(f"[ffmpeg] Не получилось скачать с {url}: {exc}", file=sys.stderr)
    where = "рядом с программой" if target_dir == local_bin_dir() \
        else f"в {target_dir}"
    raise FFmpegNotFoundError(
        "Не удалось скачать ffmpeg. Установите его вручную "
        "(https://ffmpeg.org/download.html) или положите ffmpeg.exe "
        f"в папку «bin» {where}: {local_bin_dir()}"
    )


def _download_and_extract(url: str, target_dir: Path, progress) -> Path:
    with tempfile.TemporaryDirectory(prefix="ffmpeg_dl_") as tmp:
        tmp_dir = Path(tmp)
        archive_path = tmp_dir / "ffmpeg.zip"

        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response, open(
            archive_path, "wb"
        ) as out:
            total_raw = response.headers.get("Content-Length")
            total = int(total_raw) if total_raw else None
            received = 0
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)

        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(tmp_dir / "unpacked")

        ffmpeg_bin = next(
            (tmp_dir / "unpacked").rglob(_ffmpeg_name()), None
        )
        if ffmpeg_bin is None:
            raise RuntimeError("в архиве не оказалось ffmpeg")

        target_dir.mkdir(parents=True, exist_ok=True)
        target_ffmpeg = target_dir / _ffmpeg_name()
        shutil.copy2(ffmpeg_bin, target_ffmpeg)
        # Заодно забираем ffprobe и остальное из той же папки — пригодится.
        for extra in ffmpeg_bin.parent.iterdir():
            if extra.is_file() and extra != ffmpeg_bin and not extra.suffix:
                try:
                    shutil.copy2(extra, target_dir / extra.name)
                except OSError:
                    pass

    if not _works(target_ffmpeg):
        raise RuntimeError("скачанный ffmpeg не запускается")
    return target_ffmpeg


def _not_found_message(download_hint: bool) -> str:
    hint = (
        "Запустите программу ещё раз, чтобы скачать ffmpeg автоматически, "
        "или установите его вручную: https://ffmpeg.org/download.html"
        if download_hint
        else "Скачайте ffmpeg с https://ffmpeg.org/download.html или положите "
        f"ffmpeg.exe в папку «bin» рядом с программой: {local_bin_dir()}"
    )
    if os.name != "nt":
        hint = (
            "Установите ffmpeg пакетным менеджером: "
            "macOS — `brew install ffmpeg`, Ubuntu/Debian — `sudo apt install ffmpeg`"
        )
    return f"ffmpeg не найден. {hint}"
