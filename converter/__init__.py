"""MP4 → MP3 конвертер: ядро, CLI и GUI.

Пакет не требует сторонних библиотек — только стандартную библиотеку Python
и бинарник ffmpeg, который при необходимости скачивается автоматически.
"""

from .core import (
    VIDEO_EXTENSIONS,
    CANCELLED,
    BatchSummary,
    ConvertResult,
    convert_batch,
    convert_file,
)
from .ffmpeg_setup import (
    FFMPEG_URLS_WINDOWS,
    FFmpegNotFoundError,
    ensure_ffmpeg,
    ffmpeg_available,
)
from .util import collect_videos, human_size, parse_time, unique_path, validate_bitrate

__version__ = "1.0.0"

__all__ = [
    "VIDEO_EXTENSIONS",
    "CANCELLED",
    "BatchSummary",
    "ConvertResult",
    "convert_batch",
    "convert_file",
    "FFMPEG_URLS_WINDOWS",
    "FFmpegNotFoundError",
    "ensure_ffmpeg",
    "ffmpeg_available",
    "collect_videos",
    "human_size",
    "parse_time",
    "unique_path",
    "validate_bitrate",
    "__version__",
]
