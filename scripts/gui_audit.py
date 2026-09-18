"""Графический аудит: макет без «глазами» — перекрытия, границы, выравнивание,
плюс живая конвертация через окно и скриншоты через PowerShell.

Запуск: python scripts/gui_audit.py [файл-видео]
Результат: отчёт в stdout + PNG-снимки во временной папке.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk

import converter.gui as gui_mod
from converter.gui import ConverterApp
from converter.theme import sc

SHOTS = Path(os.environ["TEMP"]) / "mp4tomp3-guiaudit"
issues: list[str] = []


def note(ok: bool, name: str, detail: str = "") -> None:
    print(f"[{'OK ' if ok else 'BUG'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        issues.append(f"{name}: {detail}")


def screenshot(tag: str) -> None:
    SHOTS.mkdir(exist_ok=True)
    ps = (
        "Add-Type -AssemblyName System.Drawing;"
        "$b = New-Object Drawing.Bitmap 2560, 1440;"
        "$g = [Drawing.Graphics]::FromImage($b);"
        "$g.CopyFromScreen(0, 0, 0, 0, $b.Size);"
        f"$b.Save('{SHOTS / (tag + '.png')}');"
        "$g.Dispose(); $b.Dispose()"
    )
    subprocess.run(["powershell", "-Command", ps], capture_output=True)


def overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def audit_layout(app: ConverterApp, tag: str) -> None:
    app.root.update_idletasks()
    app.root.update()
    problems = []

    def walk(widget, depth=0):
        try:
            kids = widget.winfo_children()
        except Exception:
            return
        boxes = []
        for kid in kids:
            try:
                if not kid.winfo_ismapped() or kid.winfo_width() < 2:
                    continue
                box = (kid.winfo_x(), kid.winfo_y(),
                       kid.winfo_width(), kid.winfo_height())
                # холсты-кнопки игнорируют друг друга по дизайну ряда
                if isinstance(kid, tk.Canvas):
                    continue
                boxes.append((kid, box))
            except Exception:
                continue
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                w1, b1 = boxes[i]
                w2, b2 = boxes[j]
                if overlaps(b1, b2):
                    problems.append(
                        f"{tag}: перекрытие {w1.winfo_class()} и {w2.winfo_class()} "
                        f"{b1} vs {b2}")
        for kid in kids:
            walk(kid, depth + 1)

    walk(app.root)

    # ничего не вылезает за пределы окна
    win_w, win_h = app.root.winfo_width(), app.root.winfo_height()
    for widget in (app.listbox, app.convert_button, app.progress):
        if widget.winfo_rootx() + widget.winfo_width() > \
                app.root.winfo_rootx() + win_w + 2:
            problems.append(f"{tag}: {widget} вылезает вправо")
        if widget.winfo_rooty() + widget.winfo_height() > \
                app.root.winfo_rooty() + win_h + 2:
            problems.append(f"{tag}: {widget} вылезает вниз")

    note(not problems, f"макет {tag}: без перекрытий и вылетов",
         "; ".join(problems[:3]))


def main() -> int:
    if SHOTS.exists():
        for f in SHOTS.glob("*.png"):
            f.unlink()
    SHOTS.mkdir(exist_ok=True)

    dialogs: list[str] = []
    gui_mod.messagebox.showerror = lambda title, msg, **k: dialogs.append(
        f"error:{msg[:40]}")
    gui_mod.messagebox.showinfo = lambda title, msg, **k: dialogs.append(
        f"info:{msg[:40]}")
    gui_mod.messagebox.askyesno = lambda title, msg, **k: (
        dialogs.append(f"yes-no:{msg[:30]}"), False)[1]

    root = tk.Tk()
    app = ConverterApp(root)
    root.geometry(f"{sc(900)}x{sc(700)}+{sc(120)}+{sc(60)}")
    root.update()

    # ждём готовности ffmpeg
    deadline = time.time() + 8
    while time.time() < deadline:
        root.update()
        if str(app.convert_button["state"]) == "normal":
            break
        time.sleep(0.05)
    note(str(app.convert_button["state"]) == "normal", "кнопка активна после ffmpeg")

    # 1. пустой список → конвертировать → инфо-окно, а не крэш
    app.start_conversion()
    root.update()
    note(any(d.startswith("info:") for d in dialogs), "пустой список даёт подсказку",
         "; ".join(dialogs[:2]))

    # 2. невалидный фрагмент
    dialogs.clear()
    app.files.append(Path("C:/tmp/тест.mp4"))
    app.marks.append("pending")
    app.start_var.set("не время")
    app.start_conversion()
    root.update()
    note(any("время" in d for d in dialogs), "мусор в поле фрагмента даёт ошибку",
         "; ".join(dialogs[:1]))
    app.start_var.set("")

    # 3. макет: обычный, минимальный (Tk сам клампит к minsize), крупный
    audit_layout(app, "обычный")
    screenshot("01-обычный")
    root.geometry("1x1")
    audit_layout(app, "минимальный")
    screenshot("02-минимальный")
    root.geometry(f"{sc(1300)}x{sc(900)}")
    audit_layout(app, "крупный")
    screenshot("03-крупный")

    # 4. длинные имена в списке (переполнение строки)
    app.files.clear(); app.marks.clear()
    long_name = "Очень длинное название видеозаписи конференции" * 3
    app.files.append(Path(f"C:/videos/{long_name}.mp4"))
    app.marks.append("pending")
    for i in range(24):
        app.files.append(Path(f"C:/videos/файл_{i:02d}.mp4"))
        app.marks.append("pending")
    app._redraw()
    root.update()
    note(app.listbox.size() == 25 and "25 файлов" in app.count_var.get(),
         "счётчик при 25 файлах", app.count_var.get())
    audit_layout(app, "25-файлов")
    screenshot("04-много-файлов")

    # 5. реальная конвертация через окно
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if video and video.is_file():
        app.files.clear(); app.marks.clear()
        app.files.append(video)
        app.marks.append("pending")
        app._redraw()
        app.start_var.set(""); app.end_var.set("")
        app.split_var.set("none")       # чистый режим, без остатков настроек
        app.split_interval_var.set("")
        app.normalize_var.set(True)
        app.start_conversion()
        midrun = False
        deadline = time.time() + 60
        while time.time() < deadline:
            root.update()
            if app.converting and str(app.stop_button["state"]) == "normal":
                midrun = True
                break
            if not app.converting and app.marks[0] == "ok":
                break
            time.sleep(0.05)
        screenshot("05-конвертация")
        deadline = time.time() + 60
        while time.time() < deadline and app.converting:
            root.update()
            time.sleep(0.05)
        root.update()
        note(app.marks[0] == "ok", "конвертация через окно завершена успехом",
             f"статус={app.marks[0]}, стоп-кнопка была видна={midrun}")
        note(midrun, "кнопка «Остановить» появлялась во время работы")
        note(str(app.stop_button["state"]) == "disabled"
             and not app.stop_button.winfo_ismapped(),
             "«Остановить» скрыта после завершения")

    screenshot("06-итог")
    print("=" * 70)
    if issues:
        print(f"ГРАФИЧЕСКИХ ПРОБЛЕМ: {len(issues)}")
        for i in issues:
            print(" -", i)
    else:
        print("ГРАФИЧЕСКИХ ПРОБЛЕМ НЕ НАЙДЕНО")
    print(f"Снимки: {SHOTS}")
    root.destroy()
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
