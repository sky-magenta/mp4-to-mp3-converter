"""Охота за багами: краевые случаи логики конвертера.

Запуск: python scripts/bughunt.py
Каждая проверка печатает [BUG] / [OK] и деталь.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter.core import OK, convert_batch, convert_file, probe_duration  # noqa: E402
from converter.ffmpeg_setup import find_ffmpeg  # noqa: E402
from converter.util import collect_videos  # noqa: E402

WORK = Path.home() / "AppData/Local/Temp" / "mp4tomp3-bughunt"
issues: list[str] = []


def note(ok: bool, name: str, detail: str = "") -> None:
    mark = "OK " if ok else "BUG"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        issues.append(name)


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    ffmpeg = find_ffmpeg()

    # -- тестовые файлы -----------------------------------------------------
    good = WORK / "нормальное видео.mp4"
    subprocess.run(
        [str(ffmpeg), "-y", "-hide_banner", "-nostdin",
         "-f", "lavfi", "-i", "testsrc=duration=20:size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(good)],
        capture_output=True, check=True)

    corrupt = WORK / "битый.mp4"
    corrupt.write_bytes(b"\x00\x00\x00 ftypisom" + b"garbage" * 1000)
    empty = WORK / "пустой.mp4"
    empty.write_bytes(b"")

    # 1. Фрагмент за пределами файла: сейчас что происходит?
    r = convert_file(good, dst_dir=WORK / "t1", start=100, end=110, ffmpeg=ffmpeg)
    dur = probe_duration(ffmpeg, r.dst) if r.dst and r.dst.is_file() else -1
    dur = -1 if dur is None else dur
    note(not (r.status == OK and dur < 0.5),
         "фрагмент за пределами файла не должен выдавать «готово»",
         f"status={r.status}, длительность={dur:.2f}с, msg={r.message}")

    # 2. Фрагмент частично за пределами (начало в файле, конец за)
    r = convert_file(good, dst_dir=WORK / "t2", start="0:15", end="0:40",
                     ffmpeg=ffmpeg)
    dur = probe_duration(ffmpeg, r.dst) if r.dst and r.dst.is_file() else -1
    note(r.status == OK and 4.5 < dur < 5.5,
         "фрагмент с хвостом за EOF обрезается по концу файла",
         f"status={r.status}, длительность={dur:.2f}с")

    # 3. Битый файл — понятная ошибка
    r = convert_file(corrupt, dst_dir=WORK / "t3", ffmpeg=ffmpeg)
    note(r.status == "failed" and ("поврежд" in r.message or "ffmpeg" in r.message),
         "битый файл даёт понятную ошибку", f"status={r.status}, msg={r.message}")

    # 4. Пустой (0 байт) файл
    r = convert_file(empty, dst_dir=WORK / "t4", ffmpeg=ffmpeg)
    note(r.status == "failed", "пустой файл даёт ошибку",
         f"status={r.status}, msg={r.message}")

    # 5. Разбивка + фрагмент одновременно: фрагмент молча игнорируется?
    #    (в GUI можно задать оба — проверяем поведение)
    from converter.splitting import split_to_tracks
    tracks = split_to_tracks(good, dst_dir=WORK / "t5", ffmpeg=ffmpeg,
                             mode="interval", interval=5)
    lens = [probe_duration(ffmpeg, t.dst) for t in tracks if t.status == OK]
    note(len(tracks) == 4 and all(abs(x - 5) < 0.5 for x in lens),
         "интервальная разбивка 20с/5с даёт 4 куска по 5с",
         f"{len(tracks)} шт, длительности={[f'{x:.1f}' for x in lens]}")

    # 6. Интервальная разбивка не зависит от min_track (он — про паузы;
    #    уточнено в --help). Файл 20с кусков по 5с при min_track=300.
    tracks = split_to_tracks(good, dst_dir=WORK / "t6", ffmpeg=ffmpeg,
                             mode="interval", interval="0:05", min_track=300)
    note(all(t.status == OK for t in tracks) and len(tracks) == 4,
         "интервальная разбивка игнорирует min_track (задокументировано)",
         f"{[t.status for t in tracks]}")

    # 7. Пустая папка при добавлении
    (WORK / "empty_dir").mkdir()
    found = collect_videos([WORK / "empty_dir"])
    note(found == [], "пустая папка даёт пустой список (без исключения)")

    # 8. Папка с не-видео файлами
    (WORK / "docs").mkdir()
    (WORK / "docs" / "report.txt").write_text("x")
    found = collect_videos([WORK / "docs"])
    note(found == [], "папка только с txt не добавляет ничего")

    # 9. Дубликаты: collect_videos дедуплицирует; в WORK лежат ещё
    #    битый.mp4 и пустой.mp4 — их папка тоже отдаёт
    once = collect_videos([good])
    twice = collect_videos([good, good, WORK])
    names = sorted(p.name for p in twice)
    note(len(twice) == len(set(twice)) and len(twice) == 3
         and names == ["битый.mp4", "нормальное видео.mp4", "пустой.mp4"],
         "повторное добавление не дублирует файлы", f"{names}")

    # 10. Конвертация одного файла дважды с rename — цепочка копий
    convert_file(good, dst_dir=WORK / "t10", ffmpeg=ffmpeg)
    r2 = convert_file(good, dst_dir=WORK / "t10", ffmpeg=ffmpeg)
    r3 = convert_file(good, dst_dir=WORK / "t10", ffmpeg=ffmpeg)
    copies = list((WORK / "t10").glob("*.mp3"))
    note(len(copies) == 3 and r3.dst.name == "нормальное видео (2).mp3",
         "политика rename создаёт (1), (2)", f"файлы={[p.name for p in copies]}")

    # 11. Русское имя + пробелы + точка в имени
    dotted = WORK / "видео.версия.2.mp4"
    shutil.copy2(good, dotted)
    r = convert_file(dotted, dst_dir=WORK / "t11", ffmpeg=ffmpeg)
    note(r.status == OK and r.dst.name == "видео.версия.2.mp3",
         "точки в имени сохраняются корректно", f"{r.dst.name if r.dst else None}")

    # 12. Отмена ДО старта в партии
    import threading
    stop = threading.Event(); stop.set()
    summary = convert_batch([good, good], dst_dir=WORK / "t12",
                            ffmpeg=ffmpeg, stop=stop)
    note(len(summary.cancelled) == 2 and not list((WORK / "t12").glob("*.mp3")),
         "отмена до старта: ничего не создано",
         f"cancelled={len(summary.cancelled)}")

    print("=" * 70)
    if issues:
        print(f"НАЙДЕНО БАГОВ: {len(issues)}")
        for name in issues:
            print(" -", name)
    else:
        print("БАГОВ НЕ НАЙДЕНО")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
