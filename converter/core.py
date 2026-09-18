"""Ядро: конвертация видео-файлов в MP3 через ffmpeg.

Один вызов ``convert_file`` = один запуск ffmpeg. Ошибки ffmpeg
переводятся на человеческий язык, прогресс внутри файла отдаётся
колбэком (используется GUI), длинные партии можно останавливать
через threading.Event.
"""

from __future__ import annotations

import ctypes
import json
import math
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .ffmpeg_setup import ensure_ffmpeg
from .util import (
    VIDEO_EXTENSIONS,  # noqa: F401  (публичное имя пакета)
    ConverterError,
    parse_time,
    unique_path,
    validate_bitrate,
)

OK = "ok"
SKIPPED = "skipped"
FAILED = "failed"
CANCELLED = "cancelled"

# Цель нормализации — стриминговый стандарт EBU R128: -16 LUFS,
# True Peak -1.5 дБ. Двухпроходный режим (linear=true по замеренным
# значениям) не добавляет динамической компрессии — так делает
# эталонный инструмент ffmpeg-normalize (github.com/slhck/ffmpeg-normalize).
_LOUDNORM_TARGET = "loudnorm=I=-16:TP=-1.5:LRA=11"

# Поле loudnorm из вывода → ключ для второго прохода.
_MEASURE_KEYS = {
    "input_i": "measured_I",
    "input_tp": "measured_TP",
    "input_lra": "measured_LRA",
    "input_thresh": "measured_thresh",
    "target_offset": "offset",
}

# (фрагмент stderr ffmpeg, понятное объяснение)
_ERROR_HINTS = (
    ("does not contain any stream", "в файле нет звуковой дорожки"),
    ("does not contain any audio", "в файле нет звуковой дорожки"),
    ("invalid data found", "файл повреждён или это не видео/аудио"),
    ("moov atom not found", "файл не дописан до конца (повреждён)"),
    ("no such file or directory", "файл не найден"),
    ("permission denied", "нет доступа к файлу (открыт в другой программе?)"),
    ("disk", "проблема с диском: нет места или нет доступа"),
)

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_OUT_TIME_RE = re.compile(r"out_time_(?:us|ms)=(\d+)")
_LOUDNORM_JSON_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}")


@dataclass
class ConvertResult:
    """Результат обработки одного файла."""

    src: Path
    dst: Path | None = None
    status: str = FAILED
    message: str = ""
    duration_sec: float | None = None
    size_bytes: int = 0

    @property
    def ok(self) -> bool:
        return self.status == OK


@dataclass
class BatchSummary:
    """Итоги пакетной конвертации."""

    results: list[ConvertResult] = field(default_factory=list)
    elapsed_sec: float = 0.0

    def _by(self, status: str) -> list[ConvertResult]:
        return [r for r in self.results if r.status == status]

    @property
    def ok(self) -> list[ConvertResult]:
        return self._by(OK)

    @property
    def skipped(self) -> list[ConvertResult]:
        return self._by(SKIPPED)

    @property
    def failed(self) -> list[ConvertResult]:
        return self._by(FAILED)

    @property
    def cancelled(self) -> list[ConvertResult]:
        return self._by(CANCELLED)

    @property
    def total_size(self) -> int:
        return sum(r.size_bytes for r in self.ok)

    def __str__(self) -> str:
        text = (
            f"Готово: {len(self.ok)}  |  Пропущено: {len(self.skipped)}  |  "
            f"Ошибок: {len(self.failed)}"
        )
        if self.cancelled:
            text += f"  |  Отменено: {len(self.cancelled)}"
        return text


def _humanize_error(stderr: str) -> str:
    lowered = stderr.lower()
    for needle, hint in _ERROR_HINTS:
        if needle in lowered:
            return hint
    # Последние строки stderr — обычно там суть.
    tail = stderr.strip().splitlines()[-1] if stderr.strip() else "неизвестная ошибка"
    return f"ffmpeg: {tail}"


def probe_duration(ffmpeg: Path, src: Path) -> float | None:
    """Длительность файла в секундах (парсим вывод `ffmpeg -i`)."""
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(src)],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _DURATION_RE.search(proc.stderr.decode("utf-8", "replace"))
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _resolve_dst(src: Path, dst: Path | None, dst_dir: Path | None,
                 overwrite: str) -> tuple[Path, str | None]:
    """Куда писать и нужно ли: (итоговый путь, причина пропуска)."""
    if dst is None:
        out_dir = dst_dir if dst_dir is not None else src.parent
        dst = out_dir / f"{src.stem}.mp3"
    dst = dst.with_suffix(".mp3")

    if dst.exists():
        if overwrite == "skip":
            return dst, "mp3 уже существует"
        if overwrite == "rename":
            dst = unique_path(dst)
    return dst, None


def _audio_filter_args(normalize: bool, mono: bool, denoise: bool,
                       measured: dict | None = None) -> list[str]:
    """Собирает -af/-ac/-ar: денойз → нормализация → моно.

    Возвращает список аргументов ffmpeg (выходных).
    """
    filters = []
    if denoise:
        # Речь/записи с фоном: срез гула снизу + FFT-шумодав.
        filters += ["highpass=f=80", "afftdn=nr=12:nf=-28"]
    if normalize:
        loudnorm = _LOUDNORM_TARGET
        linear = _linear_loudnorm_args(measured)
        if linear:
            loudnorm += ":" + linear + ":linear=true"
        filters.append(loudnorm)
    args = []
    if filters:
        args += ["-af", ",".join(filters)]
    if mono:
        args += ["-ac", "1"]
    return args


def _linear_loudnorm_args(measured: dict | None) -> str | None:
    """Аргументы linear-режима из замера, или None если данные непригодны.

    Значения в JSON — строки; «-inf» бывает у полностью тихих файлов —
    тогда честно откатываемся на однопроходный loudnorm.
    """
    if not measured:
        return None
    pairs = []
    for field, key in _MEASURE_KEYS.items():
        raw = measured.get(field)
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        pairs.append(f"{key}={value:.2f}")
    return ":".join(pairs)


def measure_loudness(ffmpeg: Path, src: Path, pre: list[str] | None = None,
                     post: list[str] | None = None,
                     audio_stream: int | None = None) -> dict | None:
    """Первый проход loudnorm: замер громкости для линейной нормализации.

    Возвращает словарь (input_i, input_tp, …) или None, если замер не удался —
    тогда convert_file честно откатится на однопроходный режим.
    """
    cmd = [
        str(ffmpeg), "-hide_banner", "-nostdin",
        *(pre or []),
        "-i", str(src),
        *(post or []),
    ]
    if audio_stream is not None:
        cmd += ["-map", f"0:a:{audio_stream}"]
    cmd += ["-af", f"{_LOUDNORM_TARGET}:print_format=json", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stderr.decode("utf-8", "replace")
    match = _LOUDNORM_JSON_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    return data if "input_i" in data else None


def _trim_args(start: float | None, end: float | None) -> tuple[list[str], list[str]]:
    """Аргументы обрезки: (до -i, после -i)."""
    pre: list[str] = []
    post: list[str] = []
    if start is not None:
        # Быстрый поиск перед входом; при перекодировании это достаточно точно.
        pre += ["-ss", f"{start:.3f}"]
    if end is not None:
        if start is not None:
            post += ["-t", f"{end - start:.3f}"]
        else:
            post += ["-to", f"{end:.3f}"]
    return pre, post


def convert_file(
    src,
    dst=None,
    dst_dir=None,
    *,
    bitrate: int = 192,
    overwrite: str = "rename",
    ffmpeg=None,
    progress: Callable[[float | None], None] | None = None,
    start=None,
    end=None,
    normalize: bool = False,
    mono: bool = False,
    denoise: bool = False,
    title_tag: bool = True,
    audio_stream: int | None = None,
    stop: threading.Event | None = None,
) -> ConvertResult:
    """Конвертирует один видео-файл в MP3.

    Аргументы:
        src: исходный файл.
        dst: точный путь результата (без .mp3 — допишется сам).
        dst_dir: папка результата (игнорируется, если задан dst).
        bitrate: битрейт, kbps (96–320).
        overwrite: «overwrite» | «skip» | «rename» — что делать с существующим mp3.
        ffmpeg: путь до ffmpeg (по умолчанию ищется/скачивается автоматически).
        progress: колбэк прогресса внутри файла (0.0–1.0 или None, если неизвестен).
        start, end: фрагмент — секунды или строка «1:30» / «1:02:03».
        normalize: выровнять громкость (двухпроходный loudnorm, EBU R128).
        mono: одноканальный звук (для речи, файл вдвое меньше).
        denoise: убрать фоновый шум (для речи: highpass + afftdn).
        title_tag: записать в mp3 тег title с именем исходного файла.
        audio_stream: номер звуковой дорожки в видео (0 = первая).
        stop: событие отмены — процесс ffmpeg будет убит, файл подчистится.
    """
    src = Path(src)
    if dst is not None:
        dst = Path(dst)
    if dst_dir is not None:
        dst_dir = Path(dst_dir)
    bitrate = validate_bitrate(bitrate)
    if overwrite not in ("overwrite", "skip", "rename"):
        raise ConverterError(
            f"Неизвестная политика перезаписи: {overwrite!r} "
            "(ожидается overwrite/skip/rename)"
        )
    try:
        start_s = parse_time(start) if start is not None else None
        end_s = parse_time(end) if end is not None else None
    except ConverterError as exc:
        return ConvertResult(src=src, status=FAILED, message=str(exc))
    if start_s is not None and end_s is not None and end_s <= start_s:
        return ConvertResult(
            src=src, status=FAILED,
            message="конец фрагмента должен быть позже начала",
        )

    if not src.is_file():
        return ConvertResult(src=src, status=FAILED, message="файл не найден")

    dst, skip_reason = _resolve_dst(src, dst, dst_dir, overwrite)
    if skip_reason:
        return ConvertResult(src=src, dst=dst, status=SKIPPED, message=skip_reason)

    if stop is not None and stop.is_set():
        return ConvertResult(src=src, dst=dst, status=CANCELLED, message="отменено")

    if ffmpeg is None:
        ffmpeg = ensure_ffmpeg()
    ffmpeg = Path(ffmpeg)

    dst.parent.mkdir(parents=True, exist_ok=True)

    pre, post = _trim_args(start_s, end_s)

    measured = None
    if normalize:
        measured = measure_loudness(ffmpeg, src, pre=pre, post=post,
                                     audio_stream=audio_stream)

    cmd = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        *pre,
        "-i", str(src),
        *post,
    ]
    if audio_stream is not None:
        cmd += ["-map", f"0:a:{audio_stream}"]
    cmd += [
        "-vn", "-c:a", "libmp3lame", "-b:a", f"{bitrate}k",
        *_audio_filter_args(normalize, mono, denoise, measured),
    ]
    if normalize:
        # linear loudnorm внутри апсемплит до 192 кГц — возвращаем стандарт.
        cmd += ["-ar", "44100"]
    if title_tag:
        cmd += ["-metadata", f"title={src.stem}"]
    if progress is not None:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd.append(str(dst))

    # Ожидаемая длительность результата — для расчёта прогресса.
    total: float | None = None
    if progress is not None:
        full = probe_duration(ffmpeg, src)
        if full is not None:
            if start_s is not None:
                full = max(full - start_s, 0.0)
            if end_s is not None:
                full = min(full, end_s - (start_s or 0.0))
            total = full or None

    try:
        returncode, stderr, was_cancelled = _execute(cmd, progress, total, stop)
    except OSError as exc:
        return ConvertResult(src=src, dst=dst, status=FAILED, message=str(exc))

    if was_cancelled:
        if dst.exists():
            dst.unlink()  # недописанный кусок нам не нужен
        return ConvertResult(src=src, dst=dst, status=CANCELLED, message="отменено")

    if returncode != 0 or not dst.is_file():
        return ConvertResult(
            src=src, dst=dst, status=FAILED, message=_humanize_error(stderr)
        )
    size = dst.stat().st_size
    if size < 1024:
        # Меньше килобайта — это заголовок без звука: фрагмент промахнулся
        # мимо файла (или запись короче секунды). Честно признаёмся.
        dst.unlink()
        return ConvertResult(
            src=src, dst=dst, status=FAILED,
            message="ничего не записано — фрагмент за пределами файла "
                    "или звук короче секунды",
        )
    return ConvertResult(
        src=src, dst=dst, status=OK, message="готово",
        size_bytes=size,
    )


_JOB_HANDLE: int | None = None


def _win_job_handle() -> int | None:
    """Job Object с KILL_ON_JOB_CLOSE: дочерние ffmpeg-процессы умирают
    вместе с нашим процессом — даже при жёстком убийстве или падении.
    Без этого закрытие окна посреди конвертации оставляет ffmpeg сиротой.
    """
    global _JOB_HANDLE
    if _JOB_HANDLE is not None:
        return _JOB_HANDLE
    if sys.platform != "win32":
        return None
    try:
        import ctypes as _ctypes

        kernel32 = _ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)

        class _IOCounters(_ctypes.Structure):
            _fields_ = [(name, _ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount",
                "WriteTransferCount", "OtherTransferCount")]

        class _BasicLimits(_ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", _ctypes.c_longlong),
                ("PerJobUserTimeLimit", _ctypes.c_longlong),
                ("LimitFlags", _ctypes.c_ulong),
                ("MinimumWorkingSetSize", _ctypes.c_size_t),
                ("MaximumWorkingSetSize", _ctypes.c_size_t),
                ("ActiveProcessLimit", _ctypes.c_ulong),
                ("Affinity", _ctypes.c_ulonglong),
                ("PriorityClass", _ctypes.c_ulong),
                ("SchedulingClass", _ctypes.c_ulong),
            ]

        class _ExtendedLimits(_ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IOCounters),
                ("ProcessMemoryLimit", _ctypes.c_size_t),
                ("JobMemoryLimit", _ctypes.c_size_t),
                ("PeakProcessMemoryUsed", _ctypes.c_size_t),
                ("PeakJobMemoryUsed", _ctypes.c_size_t),
            ]

        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job, 9, _ctypes.byref(info), _ctypes.sizeof(info))
        _JOB_HANDLE = job
        return job
    except Exception:
        return None


def _track_child_process(proc: subprocess.Popen) -> None:
    """Привязываем дочерний процесс к job-объекту, если он есть."""
    job = _win_job_handle()
    if job is None:
        return
    try:
        import ctypes

        # _handle — приватный, но стабильный атрибут CPython's Popen
        handle = int(proc._handle)
        ctypes.windll.kernel32.AssignProcessToJobObject(job, handle)
    except Exception:
        pass  # не смогли — работаем как раньше


def _execute(cmd, progress, total, stop) -> tuple[int, str, bool]:
    """Запускает ffmpeg; опционально читает `-progress pipe:1` и следит за отменой."""
    with tempfile.TemporaryFile(mode="w+") as err_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if progress is not None else subprocess.DEVNULL,
            stderr=err_file,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        _track_child_process(proc)
        try:
            if progress is None:
                while proc.poll() is None:
                    if stop is not None and stop.is_set():
                        proc.kill()
                        break
                    time.sleep(0.1)
            else:
                for line in proc.stdout:
                    match = _OUT_TIME_RE.match(line.strip())
                    if match and total:
                        done_us = int(match.group(1))
                        # В старых сборках out_time_ms на самом деле микросекунды,
                        # в новых поле называется out_time_us — оба в мкс.
                        progress(min(done_us / 1_000_000 / total, 1.0))
                    if stop is not None and stop.is_set():
                        proc.kill()
                        break
            proc.wait()
        finally:
            if proc.poll() is None:  # например, при исключении в колбэке
                proc.kill()
                proc.wait()
        err_file.seek(0)
        stderr = err_file.read()

    was_cancelled = (
        stop is not None and stop.is_set() and proc.returncode not in (0, None)
    )
    if progress is not None and not was_cancelled and proc.returncode == 0:
        progress(1.0)
    return proc.returncode, stderr, was_cancelled


def convert_batch(
    files: Iterable,
    dst_dir=None,
    *,
    bitrate: int = 192,
    overwrite: str = "rename",
    ffmpeg=None,
    parallel: int = 1,
    on_result: Callable[[ConvertResult, int, int], None] | None = None,
    on_progress: Callable[[int, str, float], None] | None = None,
    start=None,
    end=None,
    normalize: bool = False,
    mono: bool = False,
    denoise: bool = False,
    title_tag: bool = True,
    audio_stream: int | None = None,
    stop: threading.Event | None = None,
    split_mode: str | None = None,
    split_interval=None,
    split_silence_db: float = -35.0,
    split_min_silence: float = 1.5,
    split_min_track: float = 8.0,
) -> BatchSummary:
    """Конвертирует список файлов.

    ``on_result(result, index, total)`` вызывается после каждого файла
    (из рабочего потока — GUI обязан маршализовать вызовы сам).
    ``on_progress(index, name, fraction)`` — живой прогресс внутри файла
    (0.0–1.0; тоже из рабочего потока). При установленном ``stop``
    оставшиеся файлы помечаются «отменено».

    ``split_mode``: None — обычная конвертация; «silence» — разбивка
    по паузам; «interval» — разбивка кусочками ``split_interval``.
    """
    files = [Path(f) for f in files]
    if not files:
        return BatchSummary([])
    if dst_dir is not None:
        dst_dir = Path(dst_dir)
    if ffmpeg is None:
        ffmpeg = ensure_ffmpeg()
    parallel = max(1, min(parallel, len(files)))

    started = time.monotonic()

    def work(index_src: tuple[int, Path]) -> tuple[int, ConvertResult]:
        index, src = index_src
        file_progress = None
        if on_progress is not None:
            def file_progress(frac, _index=index, _name=src.name):
                on_progress(_index, _name,
                            float(frac) if frac is not None else 0.0)

            on_progress(index, src.name, 0.0)
        try:
            if split_mode:
                from .splitting import aggregate_split_results, split_to_tracks
                tracks = split_to_tracks(
                    src, dst_dir,
                    mode=split_mode, interval=split_interval,
                    threshold_db=split_silence_db,
                    min_silence=split_min_silence, min_track=split_min_track,
                    bitrate=bitrate, overwrite=overwrite, ffmpeg=ffmpeg,
                    normalize=normalize, mono=mono, denoise=denoise,
                    title_tag=title_tag, audio_stream=audio_stream, stop=stop,
                    progress=file_progress,
                )
                result = aggregate_split_results(tracks, src)
            else:
                result = convert_file(
                    src, dst_dir=dst_dir, bitrate=bitrate,
                    overwrite=overwrite, ffmpeg=ffmpeg,
                    start=start, end=end, normalize=normalize, mono=mono,
                    denoise=denoise, title_tag=title_tag,
                    audio_stream=audio_stream, stop=stop,
                    progress=file_progress,
                )
        except Exception as exc:  # страховка: одно исключение не должно
            # убивать всю партию — превращаем в честный «failed»
            result = ConvertResult(src=src, status=FAILED,
                                   message=f"внутренняя ошибка: {exc}")
        if on_result:
            on_result(result, index + 1, len(files))
        return index, result

    summary = BatchSummary()
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [pool.submit(work, item) for item in enumerate(files)]
        try:
            for future in futures:
                future.result()  # ждём выполнения; исключения погашены в work()
        except KeyboardInterrupt:
            # Ctrl+C в главном потоке: оставшиеся задачи увидят stop и быстро
            # вернут «отменено», бегущий ffmpeg будет убит в _execute.
            if stop is None:
                stop = threading.Event()
            stop.set()
            # выход из with дождётся всех задач — collection ниже безопасен

    # Собираем ровно один раз, в порядке исходного списка файлов.
    for future in futures:
        _, result = future.result()
        summary.results.append(result)

    summary.elapsed_sec = time.monotonic() - started
    return summary
