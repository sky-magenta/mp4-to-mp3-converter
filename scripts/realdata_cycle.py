"""Цикл проверок конвертера на реальных данных.

Запуск: python scripts/realdata_cycle.py <путь-до-видео>
Работает с копией во временной папке, оригинал не трогает.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter.core import OK, convert_batch, convert_file, measure_loudness, probe_duration  # noqa: E402
from converter.ffmpeg_setup import find_ffmpeg  # noqa: E402
from converter.splitting import split_to_tracks  # noqa: E402
from converter.util import human_size  # noqa: E402

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else None
WORK = Path.home() / "AppData/Local/Temp" / "mp4tomp3-realdata"

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    (passed if ok else failed).append(name)
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def main() -> int:
    if SRC is None or not SRC.is_file():
        print("Укажите видео: python scripts/realdata_cycle.py путь/до/видео.mp4")
        return 1
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    ffmpeg = find_ffmpeg()
    total_dur = probe_duration(ffmpeg, SRC)
    print(f"Файл: {SRC.name}  |  {human_size(SRC.stat().st_size)}  |  "
          f"{total_dur:.1f} с  |  ffmpeg: {ffmpeg.name}")
    print("=" * 78)

    # A. Полная конвертация ------------------------------------------------
    t0 = time.monotonic()
    r = convert_file(SRC, dst_dir=WORK / "A_full", ffmpeg=ffmpeg)
    dt = time.monotonic() - t0
    out_dur = probe_duration(ffmpeg, r.dst)
    check("A. Полная конвертация",
          r.status == OK and abs(out_dur - total_dur) < 1.5,
          f"{out_dur:.1f} с, {human_size(r.size_bytes)}, за {dt:.1f} с")

    # тег title с реальным именем
    info = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(r.dst)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stderr
    check("A2. Тег title из имени файла",
          SRC.stem in info)

    # B. Фрагмент -----------------------------------------------------------
    r = convert_file(SRC, dst_dir=WORK / "B_frag", ffmpeg=ffmpeg,
                     start="1:00", end="1:30")
    d = probe_duration(ffmpeg, r.dst)
    check("B. Фрагмент 1:00–1:30",
          r.status == OK and abs(d - 30) < 1.0, f"{d:.1f} с")

    # C. Нормализация: громкость до/после -----------------------------------
    before = measure_loudness(ffmpeg, SRC)
    r = convert_file(SRC, dst_dir=WORK / "C_norm", ffmpeg=ffmpeg, normalize=True)
    after = measure_loudness(ffmpeg, r.dst)
    bi = float(before["input_i"]); ai = float(after["input_i"])
    check("C. Двухпроходная нормализация к -16 LUFS",
          r.status == OK and abs(ai + 16) <= 1.5,
          f"было {bi:.1f} LUFS → стало {ai:.1f} LUFS")
    check("C2. Частота 44.1 кГц после loudnorm", "44100 Hz" in
          subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(r.dst)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stderr)

    # D. Речевой режим: моно + шумоподавление + 96 kbps ---------------------
    r = convert_file(SRC, dst_dir=WORK / "D_speech", ffmpeg=ffmpeg,
                     mono=True, denoise=True, bitrate=96)
    speech_info = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(r.dst)],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace").stderr
    full_size = (WORK / "A_full" / (SRC.stem + ".mp3")).stat().st_size
    check("D. Речь: моно+denoise+96 kbps",
          r.status == OK and "mono" in speech_info,
          f"{human_size(r.size_bytes)} против {human_size(full_size)} в стерео")

    # E. Разбивка по паузам на живой речи ------------------------------------
    t0 = time.monotonic()
    tracks = []
    for min_sil, db in ((0.8, -35.0), (0.5, -40.0)):
        tracks = split_to_tracks(SRC, dst_dir=WORK / "E_split", ffmpeg=ffmpeg,
                                 threshold_db=db, min_silence=min_sil,
                                 min_track=20.0, bitrate=128)
        if tracks and tracks[0].status == OK:
            break
    dt = time.monotonic() - t0
    oks = [t for t in tracks if t.status == OK]
    total_tracks = sum(probe_duration(ffmpeg, t.dst) for t in oks)
    # короткие куски подклеиваются к соседям — покрытие должно быть полным
    lens_e = [probe_duration(ffmpeg, t.dst) for t in oks]
    check("E. Разбивка по паузам (живая речь)",
          len(oks) >= 2 and total_tracks >= total_dur * 0.95
          and min(lens_e) >= 20.0,
          f"{len(oks)} дорожек, покрытие {total_tracks / total_dur * 100:.0f}% "
          f"из {total_dur:.0f} с, за {dt:.1f} с (порог {db} дБ, пауза ≥{min_sil} с)")

    # F. Разбивка по интервалу: куски по 2 минуты ------------------------------
    tracks = split_to_tracks(SRC, dst_dir=WORK / "F_split", ffmpeg=ffmpeg,
                             mode="interval", interval="2:00", bitrate=96)
    oks = [t for t in tracks if t.status == OK]
    lens = [probe_duration(ffmpeg, t.dst) for t in oks]
    expected_n = int(total_dur // 120) + (1 if total_dur % 120 > 5 else 0)
    check("F. Разбивка по интервалу 2:00",
          len(oks) == expected_n and lens[0] == 120.0 and
          all(abs(x - 120) < 1.0 for x in lens[:-1]),
          f"{len(oks)} кусков: {[f'{x:.0f}с' for x in lens]}")

    # G. Несуществующая дорожка: понятная ошибка -------------------------------
    r = convert_file(SRC, dst_dir=WORK / "G_stream", ffmpeg=ffmpeg,
                     audio_stream=5)
    check("G. Неверный номер дорожки → ошибка без трейса",
          r.status == "failed" and "ffmpeg:" in r.message, r.message[:70])

    # H. Отмена в середине партии ----------------------------------------------
    copies = []
    for i in range(3):
        c = WORK / f"H_copy{i}.mp4"
        shutil.copy2(SRC, c)
        copies.append(c)
    stop = threading.Event()

    def on_result(result, index, total):
        if index >= 1:
            stop.set()

    t0 = time.monotonic()
    summary = convert_batch(copies, dst_dir=WORK / "H_out", ffmpeg=ffmpeg,
                            bitrate=96, on_result=on_result, stop=stop)
    dt = time.monotonic() - t0
    check("H. Отмена после первого файла",
          len(summary.ok) == 1 and len(summary.cancelled) == 2,
          f"1 готов, 2 отменено, за {dt:.0f} с (полная партия заняла бы ~3×)")

    print("=" * 78)
    print(f"ИТОГ: {len(passed)} PASS, {len(failed)} FAIL")
    if failed:
        print("Провалены:", ", ".join(failed))
    print(f"Рабочие файлы: {WORK}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
