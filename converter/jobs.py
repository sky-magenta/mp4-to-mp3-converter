"""Фоновые задания конвертации — мост между ядром и внешними клиентами
(MCP-сервер для ИИ-агентов, тесты).

Задание запускается в своём потоке и живёт в реестре: клиент может
опрашивать статус (живой прогресс внутри файла, готовые результаты)
и останавливать выполнение. Только стандартная библиотека.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .core import convert_batch
from .util import ConverterError, parse_time, validate_bitrate

_SPLIT_MODES = (None, "silence", "interval")


@dataclass
class ConvertJob:
    job_id: str
    files: list[Path]
    options: dict
    status: str = "running"          # running | done | error | cancelled
    error: str = ""
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    done_count: int = 0
    current_index: int = 0
    current_name: str = ""
    fraction: float = 0.0            # внутри текущего файла, 0..1
    results: list[dict] = field(default_factory=list)
    stop_event: threading.Event = field(default_factory=threading.Event)

    @property
    def overall(self) -> float:
        """Общий прогресс 0..1 с учётом текущего файла."""
        if not self.files:
            return 1.0
        return min((self.done_count + self.fraction) / len(self.files), 1.0)

    @property
    def elapsed_sec(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at

    def snapshot(self) -> dict:
        """Состояние задания для внешнего клиента (JSON-сериализуемо)."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "total": len(self.files),
            "done": self.done_count,
            "current": {
                "index": self.current_index,
                "name": self.current_name,
                "fraction": round(self.fraction, 4),
            },
            "overall": round(self.overall, 4),
            "elapsed_sec": round(self.elapsed_sec, 1),
            "eta_sec": (round(self.elapsed_sec * (1 - self.overall) / self.overall, 1)
                        if self.overall > 0.02 and self.status == "running"
                        else None),
            "error": self.error or None,
            "results": list(self.results),
        }


class JobManager:
    """Реестр заданий. Один инстанс на процесс (например, MCP-сервер)."""

    def __init__(self) -> None:
        self._jobs: dict[str, ConvertJob] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ старт
    def start(self, files, output_dir=None, *, bitrate: int = 192,
              normalize: bool = False, mono: bool = False,
              denoise: bool = False, start=None, end=None,
              split_mode: str | None = None, split_interval=None,
              overwrite: str = "rename",
              audio_stream: int | None = None,
              split_silence_db: float = -35.0,
              split_min_silence: float = 1.5,
              split_min_track: float = 8.0) -> str:
        """Проверяет аргументы и запускает задание; возвращает job_id."""
        files = [Path(f) for f in files]
        if not files:
            raise ConverterError("список файлов пуст")
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            raise ConverterError(
                "файлы не найдены: " + ", ".join(missing[:5]))
        bitrate = validate_bitrate(bitrate)
        start_s = parse_time(start) if start is not None else None
        end_s = parse_time(end) if end is not None else None
        if start_s is not None and end_s is not None and end_s <= start_s:
            raise ConverterError("конец фрагмента должен быть позже начала")
        if split_mode not in _SPLIT_MODES:
            raise ConverterError(
                f"неизвестный режим разбивки: {split_mode!r} "
                "(ожидается silence/interval/None)")
        if split_mode == "interval":
            if split_interval is None:
                raise ConverterError(
                    "для разбивки по времени укажите split_interval, "
                    "например «10:00» или 600")
            split_interval = parse_time(split_interval)
        if overwrite not in ("overwrite", "skip", "rename"):
            raise ConverterError(
                "overwrite должен быть overwrite/skip/rename")

        job = ConvertJob(
            job_id=uuid.uuid4().hex[:12], files=files,
            options=dict(bitrate=bitrate, normalize=normalize, mono=mono,
                         denoise=denoise, start=start, end=end,
                         split_mode=split_mode, split_interval=split_interval,
                         overwrite=overwrite, audio_stream=audio_stream,
                         split_silence_db=split_silence_db,
                         split_min_silence=split_min_silence,
                         split_min_track=split_min_track,
                         dst_dir=Path(output_dir) if output_dir else None),
        )
        with self._lock:
            self._jobs[job.job_id] = job

        def on_result(result, index, _total):
            job.done_count = index
            job.results.append({
                "src": str(result.src),
                "dst": str(result.dst) if result.dst else None,
                "status": result.status,
                "size_bytes": result.size_bytes,
                "message": result.message or None,
            })

        def on_progress(index, name, fraction):
            job.current_index = index
            job.current_name = name
            job.fraction = fraction

        def worker():
            try:
                o = job.options
                convert_batch(
                    job.files, dst_dir=o["dst_dir"], bitrate=o["bitrate"],
                    overwrite=o["overwrite"], on_result=on_result,
                    on_progress=on_progress, start=o["start"], end=o["end"],
                    normalize=o["normalize"], mono=o["mono"],
                    denoise=o["denoise"], audio_stream=o["audio_stream"],
                    stop=job.stop_event, split_mode=o["split_mode"],
                    split_interval=o["split_interval"],
                    split_silence_db=o["split_silence_db"],
                    split_min_silence=o["split_min_silence"],
                    split_min_track=o["split_min_track"],
                )
                job.fraction = 0.0
                job.status = "cancelled" if job.stop_event.is_set() else "done"
            except ConverterError as exc:
                job.status = "error"
                job.error = str(exc)
            except Exception as exc:  # неожиданное — не роняем сервер
                job.status = "error"
                job.error = f"внутренняя ошибка: {exc}"
            finally:
                job.finished_at = time.monotonic()

        threading.Thread(target=worker, daemon=True,
                         name=f"job-{job.job_id}").start()
        return job.job_id

    # ------------------------------------------------------------------ опрос
    def status(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"задание {job_id!r} не найдено")
        return job.snapshot()

    def stop(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"задание {job_id!r} не найдено")
        if job.status == "running":
            job.stop_event.set()
        return job.snapshot()

    def list(self) -> list[dict]:
        return [j.snapshot() for j in self._jobs.values()]
