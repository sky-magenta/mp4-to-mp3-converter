"""Вспомогательные функции: пути, битрейт, сбор файлов.

Здесь нет ничего, что запускает ffmpeg, — модуль тестируется без него.
"""

from __future__ import annotations

import re
from pathlib import Path

# Контейнеры, из которых умеем вытаскивать звук. Инструмент называется
# «MP4 → MP3», но ffmpeg бесплатно справляется и с остальными — запрещать
# смысла нет.
VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm",
    ".ts", ".mts", ".m2ts", ".wmv", ".flv", ".3gp", ".ogv",
}

VALID_BITRATES = (96, 128, 160, 192, 256, 320)


class ConverterError(Exception):
    """Базовая ошибка конвертера с понятным пользователю сообщением."""


def validate_bitrate(value: int | str) -> int:
    """Возвращает битрейт в kbps или бросает ConverterError."""
    try:
        bitrate = int(str(value).strip().rstrip("kK"))
    except (TypeError, ValueError):
        raise ConverterError(
            f"Битрейт должен быть числом, получено: {value!r}"
        ) from None
    if bitrate not in VALID_BITRATES:
        allowed = ", ".join(map(str, VALID_BITRATES))
        raise ConverterError(
            f"Битрейт {bitrate} не поддерживается. Допустимо: {allowed} kbps"
        )
    return bitrate


_TIME_PART_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


def parse_time(value) -> float:
    """Парсит момент времени в секундах.

    Понимает секунды («90», «90.5», «90,5»), М:СС («1:30»), Ч:ММ:СС («1:02:03»).
    """
    if isinstance(value, bool):
        raise ConverterError(f"Некорректное время: {value!r}")
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds < 0:
            raise ConverterError("Время не может быть отрицательным")
        return seconds

    text = str(value).strip()
    if not text:
        raise ConverterError("Пустое значение времени")

    parts = text.split(":")
    if len(parts) > 3 or not all(_TIME_PART_RE.match(p) for p in parts):
        raise ConverterError(
            f"Не понимаю время «{text}». Примеры: 90, 1:30, 1:02:03"
        )

    numbers = [float(p.replace(",", ".")) for p in parts]
    if any(n >= 60 and i < len(numbers) - 1 for i, n in enumerate(numbers)):
        raise ConverterError(
            f"Не понимаю время «{text}». Примеры: 90, 1:30, 1:02:03"
        )

    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def human_size(num_bytes: int | float) -> str:
    """Размер файла в человекочитаемом виде: «2.3 МБ»."""
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            if unit == "Б":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def ru_plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма множественного числа: ru_plural(2, 'дорожка', 'дорожки', 'дорожек')."""
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many


def human_time(seconds: float) -> str:
    """Секунды в «М:СС» / «Ч:ММ:СС»: 65 → «1:05», 3665 → «1:01:05»."""
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def unique_path(path: Path) -> Path:
    """Если файл существует, добавляет « (1)», « (2)» и т.д. перед расширением."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(1, 10_000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise ConverterError(
        f"Не удалось подобрать свободное имя для {path} — слишком много копий"
    )


def collect_videos(paths, recursive: bool = False) -> list[Path]:
    """Собирает видео-файлы из списка файлов и папок.

    Файлы добавляются как есть (любого типа — пусть ffmpeg сам объяснит,
    если это не видео), из папок выбираются только известные расширения.
    Порядок стабильный, дубликаты убраны.
    """
    seen: set[Path] = set()
    result: list[Path] = []

    def add(file_path: Path) -> None:
        resolved = file_path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)

    for raw in paths:
        path = Path(raw)
        if path.is_file():
            add(path)
        elif path.is_dir():
            pattern = "**/*" if recursive else "*"
            for entry in sorted(path.glob(pattern)):
                if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
                    add(entry)
        else:
            raise ConverterError(f"Не найден файл или папка: {path}")

    return result
