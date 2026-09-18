"""Разбивка длинной записи на отдельные дорожки.

Два режима:
- «по тишине» — ffmpeg `silencedetect` находит паузы, режем по их серединам
  (концерты, миксы, подряд записанные песни);
- «по интервалу» — фиксированные куски заданной длины (аудиокниги, лекции).

Сами дорожки кодируются обычным ``convert_file`` — работают все опции
(битрейт, нормализация, моно, политика перезаписи, отмена).
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from .core import OK, ConvertResult, convert_file, probe_duration
from .util import ConverterError, parse_time, ru_plural

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")

# Порог тишины и минимальная длина паузы по умолчанию. Для записей с
# аплодисментами порог стоит поднять (примерно до -25 дБ).
DEFAULT_SILENCE_DB = -35.0
DEFAULT_SILENCE_MIN = 1.5
DEFAULT_MIN_TRACK = 8.0


def detect_silences(ffmpeg: Path, src: Path, *,
                    threshold_db: float = DEFAULT_SILENCE_DB,
                    min_dur: float = DEFAULT_SILENCE_MIN,
                    audio_stream: int | None = None,
                    ) -> list[tuple[float, float]]:
    """Список пауз (начало, конец) в секундах — вывод silencedetect."""
    import subprocess

    cmd = [str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(src)]
    if audio_stream is not None:
        cmd += ["-map", f"0:a:{audio_stream}"]
    cmd += [
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_dur}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    if proc.returncode != 0:
        raise ConverterError("не удалось проанализировать файл на паузы")

    text = proc.stderr.decode("utf-8", "replace")
    starts = [float(m) for m in _SILENCE_START_RE.findall(text)]
    ends = [float(m) for m in _SILENCE_END_RE.findall(text)]

    silences: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else None
        silences.append((start, end))
    return silences


def segments_from_silences(
    silences: list[tuple[float, float | None]],
    total: float,
    min_track: float = DEFAULT_MIN_TRACK,
) -> list[tuple[float, float]]:
    """Паузы → дорожки: режем по серединам пауз, обрезаем тишину по краям.

    Куски короче ``min_track`` подклеиваются к соседним — покрытие
    по времени сохраняется целиком, содержание не теряется.
    """
    # Незакрытая пау значит «тишина до конца файла».
    bounded = []
    for start, end in silences:
        end = total if end is None else min(end, total)
        if start < 0:
            start = 0.0
        if end - start > 0:
            bounded.append((start, end))

    time_start = 0.0
    if bounded and bounded[0][0] <= 0.1:
        time_start = bounded[0][1]
    time_end = total
    if bounded and bounded[-1][1] >= total - 0.1:
        time_end = bounded[-1][0]

    midpoints = [
        (start + end) / 2
        for start, end in bounded
        if time_start < (start + end) / 2 < time_end
    ]
    if not midpoints:
        # Нет внутренних точек реза — разбивать нечего.
        return []

    cuts = [time_start] + midpoints + [time_end]
    raw = list(zip(cuts, cuts[1:]))

    # Короткие куски не выбрасываем (терять запись нельзя), а подклеиваем
    # к соседней дорожке — покрытие по времени остаётся стопроцентным.
    merged: list[list[float]] = []
    for a, b in raw:
        if merged and b - a < min_track:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    # Первому куску приклеиваться не к чему — вливаем его в следующий.
    if len(merged) >= 2 and merged[0][1] - merged[0][0] < min_track:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    if len(merged) >= 2 and merged[-1][1] - merged[-1][0] < min_track:
        merged[-2][1] = merged[-1][1]
        merged.pop()

    return [(a, b) for a, b in merged]


def interval_segments(total: float, step: float) -> list[tuple[float, float]]:
    """Дорожки фиксированной длины: (0, step), (step, 2·step), … до конца."""
    if step <= 0:
        raise ConverterError("интервал разбивки должен быть больше нуля")
    segments = []
    position = 0.0
    while position < total - 0.01:
        segments.append((position, min(position + step, total)))
        position += step
    return segments


def split_to_tracks(
    src,
    dst_dir=None,
    *,
    mode: str = "silence",
    interval=None,
    threshold_db: float = DEFAULT_SILENCE_DB,
    min_silence: float = DEFAULT_SILENCE_MIN,
    min_track: float = DEFAULT_MIN_TRACK,
    bitrate: int = 192,
    overwrite: str = "rename",
    ffmpeg=None,
    normalize: bool = False,
    mono: bool = False,
    denoise: bool = False,
    title_tag: bool = True,
    audio_stream: int | None = None,
    stop: threading.Event | None = None,
    progress=None,
) -> list[ConvertResult]:
    """Разбивает src на отдельные mp3-дорожки, возвращает результаты.

    mode: «silence» (по паузам) или «interval» (по интервалу, заданному
    в ``interval`` — секунды или строка «10:00»).
    """
    src = Path(src)
    if dst_dir is not None:
        dst_dir = Path(dst_dir)
    if mode not in ("silence", "interval"):
        raise ConverterError(f"неизвестный режим разбивки: {mode!r}")
    if mode == "interval" and interval is None:
        raise ConverterError("для разбивки по интервалу укажите интервал")

    if stop is not None and stop.is_set():
        return [ConvertResult(src=src, status="cancelled", message="отменено")]

    if ffmpeg is None:
        from .ffmpeg_setup import ensure_ffmpeg
        ffmpeg = ensure_ffmpeg()
    ffmpeg = Path(ffmpeg)

    total = probe_duration(ffmpeg, src)
    if not total:
        return [ConvertResult(src=src, status="failed",
                              message="не удалось определить длительность")]

    if mode == "interval":
        step = parse_time(interval)
        segments = interval_segments(total, step)
        if len(segments) == 1:
            return [ConvertResult(
                src=src, status="failed",
                message=f"файл короче интервала ({total:.0f} с)",
            )]
    else:
        try:
            silences = detect_silences(
                ffmpeg, src, threshold_db=threshold_db,
                min_dur=min_silence, audio_stream=audio_stream,
            )
        except ConverterError as exc:
            return [ConvertResult(src=src, status="failed", message=str(exc))]
        segments = segments_from_silences(silences, total, min_track)
        if not segments:
            return [ConvertResult(
                src=src, status="failed",
                message="паузы не найдены — файл не разбить (уменьшите порог тишины)",
            )]

    out_dir = dst_dir if dst_dir is not None else src.parent
    width = 2 if len(segments) < 100 else 3

    results = []
    for i, (start, end) in enumerate(segments, start=1):
        if stop is not None and stop.is_set():
            results.append(ConvertResult(src=src, status="cancelled",
                                         message="отменено"))
            break
        dst = out_dir / f"{src.stem} {i:0{width}d}.mp3"
        # прогресс файла = доля готовых дорожек + доля текущей дорожки
        base, weight = (i - 1) / len(segments), 1 / len(segments)
        track_progress = None
        if progress is not None:
            def track_progress(frac, _base=base, _weight=weight):
                progress(_base + _weight * (float(frac) if frac is not None
                                            else 0.0))
        results.append(convert_file(
            src, dst=dst,
            bitrate=bitrate, overwrite=overwrite, ffmpeg=ffmpeg,
            start=start, end=end,
            normalize=normalize, mono=mono, denoise=denoise,
            title_tag=title_tag, audio_stream=audio_stream, stop=stop,
            progress=track_progress,
        ))
    return results


def aggregate_split_results(results: list[ConvertResult], src) -> ConvertResult:
    """Сворачивает список дорожек в один результат для отчёта по файлу."""
    ok = [r for r in results if r.status == OK]
    if ok:
        return ConvertResult(
            src=src, dst=ok[0].dst, status=OK,
            message=f"{len(ok)} {ru_plural(len(ok), 'дорожка', 'дорожки', 'дорожек')}",
            size_bytes=sum(r.size_bytes for r in ok),
        )
    if any(r.status == "cancelled" for r in results):
        return ConvertResult(src=src, status="cancelled", message="отменено")
    if all(r.status == "skipped" for r in results) and results:
        return ConvertResult(src=src, status="skipped",
                             message="mp3 уже существуют")
    return results[0] if results else ConvertResult(
        src=src, status="failed", message="не удалось разбить")
