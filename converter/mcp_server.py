"""MCP-сервер: управление конвертером из ИИ-агента (Model Context Protocol).

Запуск (stdio):  python -m converter.mcp   или   mp4-to-mp3-mcp
Требует пакет `mcp`:  pip install "mp4-to-mp3-converter[mcp]"  или  pip install mcp

Инструменты:
  check_ffmpeg / install_ffmpeg / find_videos / probe_media /
  convert (фоновое задание) / job_status / job_stop / jobs_list

Пример конфигурации клиента (Claude Desktop, config.json):

  "mcpServers": {
    "mp4-to-mp3": {
      "command": "python",
      "args": ["D:\\\\путь\\\\mp4-to-mp3-converter\\\\mcp_server.py"]
    }
  }
"""

from __future__ import annotations

import sys

from .ffmpeg_setup import ensure_ffmpeg, find_ffmpeg
from .jobs import JobManager
from .util import ConverterError, collect_videos, human_size

_manager = JobManager()


def _need_ffmpeg_hint() -> str:
    return ("ffmpeg не найден — вызовите инструмент install_ffmpeg "
            "или положите ffmpeg.exe в папку bin рядом с программой")


def create_server():
    """Собирает FastMCP-сервер с инструментами (отдельно от запуска —
    так проще тестировать)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Нужен пакет `mcp`: pip install mcp "
            "(или pip install \"mp4-to-mp3-converter[mcp]\")"
        ) from exc

    mcp = FastMCP("mp4-to-mp3", instructions=(
        "Конвертер MP4 → MP3. Типовой сценарий: check_ffmpeg → "
        "(install_ffmpeg, если нет) → find_videos/probe_media → "
        "convert → опрашивать job_status до status=done/cancelled/error. "
        "Прогресс: overall 0..1, eta_sec — оценка остатка."
    ))

    # ------------------------------------------------------------ ffmpeg
    @mcp.tool()
    def check_ffmpeg() -> dict:
        """Проверить, установлен ли ffmpeg, и вернуть путь к нему."""
        found = find_ffmpeg()
        return {"installed": found is not None,
                "path": str(found) if found else None}

    @mcp.tool()
    def install_ffmpeg() -> dict:
        """Скачать и установить ffmpeg (одноразово, ~80 МБ).

        Кладёт ffmpeg рядом с программой (портативно); если папка
        только для чтения — в профиль пользователя. Занимает 1–5 минут.
        """
        try:
            path = ensure_ffmpeg()
        except ConverterError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": str(path)}

    # ------------------------------------------------------------ файлы
    @mcp.tool()
    def find_videos(folder: str, recursive: bool = False) -> dict:
        """Найти видео-файлы в папке (mp4/mkv/mov/avi/webm/m4v и т.п.).

        recursive=True — искать и в подпапках.
        """
        files = collect_videos([folder], recursive=recursive)
        return {"count": len(files), "files": [str(f) for f in files]}

    @mcp.tool()
    def probe_media(path: str) -> dict:
        """Длительность (сек) и размер файла; нужен ffmpeg."""
        from pathlib import Path
        src = Path(path)
        if not src.is_file():
            return {"error": f"файл не найден: {path}"}
        ffmpeg = find_ffmpeg()
        if ffmpeg is None:
            return {"error": _need_ffmpeg_hint()}
        from .core import probe_duration
        duration = probe_duration(ffmpeg, src)
        return {"path": str(src), "duration_sec": duration,
                "size_bytes": src.stat().st_size,
                "size_human": human_size(src.stat().st_size)}

    # ------------------------------------------------------------ задания
    @mcp.tool()
    def convert(
        files: list[str],
        output_dir: str | None = None,
        bitrate: int = 192,
        normalize: bool = False,
        mono: bool = False,
        denoise: bool = False,
        start: str | None = None,
        end: str | None = None,
        split_mode: str | None = None,
        split_interval: str | None = None,
        overwrite: str = "rename",
        audio_stream: int | None = None,
    ) -> dict:
        """Запустить конвертацию видео в MP3 как фоновое задание.

        files — пути к видео; output_dir — куда класть mp3 (по умолчанию
        рядом с исходниками). bitrate 96–320. normalize — выровнять
        громкость (EBU R128, двухпроходный); mono — одноканальный звук
        для речи; denoise — убрать фоновый шум. start/end — фрагмент,
        «1:30» или секунды. split_mode: silence (по паузам) или interval
        (кусками по split_interval, например «10:00»). overwrite:
        rename | overwrite | skip. Возвращает job_id — дальше опрашивайте
        job_status.
        """
        try:
            job_id = _manager.start(
                files, output_dir, bitrate=bitrate, normalize=normalize,
                mono=mono, denoise=denoise, start=start, end=end,
                split_mode=split_mode, split_interval=split_interval,
                overwrite=overwrite, audio_stream=audio_stream)
        except ConverterError as exc:
            return {"ok": False, "error": str(exc)}
        job = _manager.status(job_id)
        return {"ok": True, "job_id": job_id, "total": job["total"],
                "hint": "опрашивайте job_status(job_id)"}

    @mcp.tool()
    def job_status(job_id: str) -> dict:
        """Статус задания: живой прогресс, результаты по файлам, оценка
        остатка (eta_sec). status: running | done | cancelled | error."""
        try:
            return _manager.status(job_id)
        except KeyError as exc:
            return {"error": str(exc.args[0])}

    @mcp.tool()
    def job_stop(job_id: str) -> dict:
        """Остановить выполняющееся задание (текущий файл прервётся)."""
        try:
            return _manager.stop(job_id)
        except KeyError as exc:
            return {"error": str(exc.args[0])}

    @mcp.tool()
    def jobs_list() -> dict:
        """Все задания этого запуска сервера (id, статус, прогресс)."""
        return {"count": len(_manager.list()),
                "jobs": _manager.list()}

    return mcp


def main() -> int:
    try:
        server = create_server()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    server.run()  # stdio-транспорт
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
