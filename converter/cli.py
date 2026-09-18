"""Командная строка: python cli.py видео.mp4 [папка ...] [-o OUT] [-b 192]."""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from . import __version__
from .core import OK, convert_batch
from .ffmpeg_setup import ensure_ffmpeg
from .util import ConverterError, collect_videos, human_size, parse_time, validate_bitrate

_MARK = {OK: "+", "skipped": "=", "failed": "x", "cancelled": "-"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mp4tomp3",
        description="Конвертер видео (MP4 и др.) в MP3. Без лишних зависимостей.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "inputs", nargs="*",
        help="видео-файлы и/или папки с ними",
    )
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="папка для mp3 (по умолчанию — рядом с исходником)")
    parser.add_argument("-b", "--bitrate", default=192,
                        help="битрейт kbps: 96, 128, 160, 192, 256, 320")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="искать в подпапках")
    parser.add_argument("--overwrite", choices=("rename", "overwrite", "skip"),
                        default="rename",
                        help="что делать с существующими mp3")
    parser.add_argument("-j", "--parallel", type=int, default=1,
                        help="сколько файлов конвертировать одновременно")
    parser.add_argument("--start", default=None,
                        help="начало фрагмента: секунды или М:СС, Ч:ММ:СС")
    parser.add_argument("--end", default=None,
                        help="конец фрагмента: секунды или М:СС, Ч:ММ:СС")
    parser.add_argument("--normalize", action="store_true",
                        help="выровнять громкость (двухпроходный loudnorm, EBU R128)")
    parser.add_argument("--mono", action="store_true",
                        help="одноканальный звук — для речи, файл заметно меньше")
    parser.add_argument("--denoise", action="store_true",
                        help="убрать фоновый шум (для речи: highpass + afftdn)")
    parser.add_argument("--audio-stream", type=int, default=None, metavar="N",
                        help="номер звуковой дорожки в видео (0 = первая)")
    split = parser.add_argument_group("разбивка на дорожки")
    split.add_argument("--split-silence", action="store_true",
                       help="разбить по паузам (концерты, миксы, подряд идущие песни)")
    split.add_argument("--split-every", default=None, metavar="TIME",
                       help="разбить кусочками заданной длины: 10:00, 300, …")
    split.add_argument("--silence-db", type=float, default=-35.0,
                       help="порог тишины в дБ (для аплодисментов попробуйте -25)")
    split.add_argument("--silence-min", type=float, default=1.5,
                       help="минимальная длина паузы, сек (для речи 0.5–0.8)")
    split.add_argument("--min-track", type=float, default=8.0,
                       help="минимальная длина дорожки, сек (только для --split-silence)")
    parser.add_argument("--no-metadata", action="store_true",
                        help="не записывать тег title с именем файла")
    parser.add_argument("--setup-ffmpeg", action="store_true",
                        help="только скачать/проверить ffmpeg и выйти")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.epilog = (
        "Автор: Софья Смирнова · https://t.me/forgednotwritten · "
        "https://damascus-ink.ru — блог о праве, ИИ и LegalTech"
    )
    return parser


def _print_progress(received: int, total: int | None) -> None:
    if total:
        mb_done, mb_total = received / 1e6, total / 1e6
        bar_width = 24
        filled = int(bar_width * received / total)
        bar = "#" * filled + "-" * (bar_width - filled)
        sys.stdout.write(f"\r  ffmpeg: [{bar}] {mb_done:6.1f}/{mb_total:.1f} МБ")
        sys.stdout.flush()
    else:
        sys.stdout.write(f"\r  ffmpeg: скачано {received / 1e6:.1f} МБ")
        sys.stdout.flush()


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.setup_ffmpeg:
            ffmpeg = ensure_ffmpeg(progress=_print_progress)
            print(f"\nffmpeg найден: {ffmpeg}")
            return 0

        if not args.inputs:
            parser.error("укажите хотя бы один файл или папку")

        bitrate = validate_bitrate(args.bitrate)
        files = collect_videos(args.inputs, recursive=args.recursive)
        if not files:
            print("Подходящих видео-файлов не найдено.")
            return 0

        if args.split_silence and args.split_every:
            parser.error("--split-silence и --split-every нельзя совмещать")
        split_mode = None
        split_interval = None
        if args.split_silence:
            split_mode = "silence"
        elif args.split_every:
            split_mode = "interval"
            split_interval = parse_time(args.split_every)

        print(f"ffmpeg: {ensure_ffmpeg(progress=_print_progress)}")
        sys.stdout.write("\n")

        details = []
        if args.start or args.end:
            details.append(f"фрагмент {args.start or '0'}–{args.end or 'конец'}")
        if args.normalize:
            details.append("нормализация громкости")
        if args.mono:
            details.append("моно")
        if args.denoise:
            details.append("шумоподавление")
        if split_mode == "silence":
            details.append(f"разбивка по паузам (порог {args.silence_db} дБ)")
        elif split_mode == "interval":
            details.append(f"разбивка по {args.split_every}")
        extras = f" ({', '.join(details)})" if details else ""
        print(f"Файлов к конвертации: {len(files)} (битрейт {bitrate} kbps){extras}\n")

        stop = threading.Event()

        def on_result(result, index, total):
            mark = _MARK.get(result.status, "?")
            line = f"[{index}/{total}] {mark} {result.src.name} — {result.message}"
            if result.status == OK and result.size_bytes:
                line += f", {human_size(result.size_bytes)}"
                if result.dst:
                    line += f"  ->  {result.dst}"
            print(line)

        try:
            summary = convert_batch(
                files,
                dst_dir=args.output,
                bitrate=bitrate,
                overwrite=args.overwrite,
                parallel=args.parallel,
                on_result=on_result,
                start=args.start,
                end=args.end,
                normalize=args.normalize,
                mono=args.mono,
                denoise=args.denoise,
                title_tag=not args.no_metadata,
                audio_stream=args.audio_stream,
                stop=stop,
                split_mode=split_mode,
                split_interval=split_interval,
                split_silence_db=args.silence_db,
                split_min_silence=args.silence_min,
                split_min_track=args.min_track,
            )
        except KeyboardInterrupt:
            stop.set()
            print("\nОстановлено пользователем…", file=sys.stderr)
            return 130

        print(f"\n{summary}")
        if summary.ok:
            print(
                f"Создано mp3: {len(summary.ok)} шт., "
                f"{human_size(summary.total_size)} "
                f"за {summary.elapsed_sec:.1f} с"
            )
        if summary.failed:
            print("\nНе получилось:")
            for r in summary.failed:
                print(f"  {r.src} — {r.message}")
        return 1 if summary.failed else 0

    except ConverterError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
