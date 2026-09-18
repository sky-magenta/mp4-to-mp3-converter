"""Запуск окна конвертера: python gui.py (или двойной клик по START.bat).

Отдельный файл-заглушка нужен, чтобы при запуске через pythonw (без консоли)
любая ошибка старта всё равно была видна — нативным окошком Windows и лог-файлом.
"""

import os
import sys
import traceback
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):  # собранный exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _prepare_streams() -> Path:
    """В оконом exe (pythonw / --windowed) stdout и stderr равны None.

    Перенаправляем их в лог-файл рядом с программой, иначе первый же
    print или трейсбек молча убивает процесс.
    """
    log = _base_dir() / "mp4tomp3.log"
    if sys.stdout is None or sys.stderr is None:
        try:
            stream = open(log, "a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = stream
            if sys.stderr is None:
                sys.stderr = stream
        except OSError:
            pass
    return log


def _show_error(text: str) -> None:
    log = _base_dir() / "mp4tomp3.log"
    try:
        log.write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:  # нативное окно Windows, работает даже без tkinter
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, text + f"\n\nПодробности: {log.name}", "MP4 → MP3 — ошибка", 0x10,
        )
    except Exception:
        print(text, file=sys.stderr)


def main() -> int:
    _prepare_streams()
    try:
        from converter.gui import main as run_app
    except Exception:
        _show_error(
            "Не удалось загрузить программу.\n\n"
            + traceback.format_exc(limit=3)
        )
        return 1
    try:
        return run_app()
    except Exception:
        _show_error("Программа упала с ошибкой.\n\n" + traceback.format_exc(limit=5))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
