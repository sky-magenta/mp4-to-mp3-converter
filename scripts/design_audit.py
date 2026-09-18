"""Дизайн-аудит: реальные кадры окна (PrintWindow, без захвата экрана),
палитра по токенам diff.legal и проверка подключения фирменных шрифтов.

Запуск: python scripts/design_audit.py
Результат: отчёт в stdout + PNG-кадры во временной папке
(%TEMP%\\mp4tomp3-design). Каждый кадр проходит палитральную сверку:
присутствие обязательных фирменных цветов и поиск доминирующих
нефирменных (признак «серого виджета по умолчанию»).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from collections import Counter
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk
import tkinter.font as tkfont

import converter.gui as gui_mod
from converter import theme
from converter.ffmpeg_setup import ensure_ffmpeg
from converter.gui import ConverterApp

SHOTS = Path(os.environ["TEMP"]) / "mp4tomp3-design"
issues: list[str] = []

BRAND = {
    theme.PAPER: "бумага", theme.INK: "чернила", theme.GRAPHITE: "графит",
    theme.LINE: "линия", theme.ADDED: "добавлено", theme.REMOVED: "удалено",
    theme.DARK: "тёмная поверхность", theme.SUBTLE: "подложка",
    theme.CARD: "карточка", theme.BORDER: "рамка",
    theme.FIELD_BORDER: "рамка поля", theme.INK_HOVER: "наведение кнопки",
    theme.ON_DARK_TEXT: "текст на тёмном", theme.ON_DARK_MUTED: "втор. на тёмном",
    theme.ON_DARK_ADDED: "добавлено на тёмном",
    theme.ON_DARK_REMOVED: "удалено на тёмном",
}


def note(ok: bool, name: str, detail: str = "") -> None:
    print(f"[{'OK ' if ok else 'BUG'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        issues.append(f"{name}: {detail}")


# --- захват окна через PrintWindow (работает без доступа к экрану) ------

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.GetWindowDC.restype = wintypes.HDC
user32.GetAncestor.restype = wintypes.HWND
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, wintypes.INT, wintypes.INT]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT]
gdi32.GetDIBits.restype = wintypes.INT
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def window_hwnd(root: tk.Misc) -> int:
    return user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT


def capture(root: tk.Misc, tag: str) -> Path:
    from PIL import Image

    root.update_idletasks()
    root.update()
    time.sleep(0.12)  # пусть tk доделает отрисовку
    root.update()

    hwnd = window_hwnd(root)
    # только клиентская область: рамка/тень окна PrintWindow красит в чёрный
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth, bmi.biHeight = w, -h  # сверху вниз
    bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0  # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    path = SHOTS / f"{tag}.png"
    img.save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    note(ok != 0, f"кадр «{tag}» снят PrintWindow", f"PrintWindow={ok}")
    return path


# --- палитральная сверка ------------------------------------------------

def audit_palette(tag: str, path: Path, required: list[str]) -> None:
    """Обязательные фирменные цвета присутствуют; посторонних доминант нет."""
    from PIL import Image

    img = Image.open(path).convert("RGB")
    counts = Counter(img.getdata())
    total = img.width * img.height
    missing = []
    for hexcolor, name in BRAND.items():
        rgb = tuple(int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
        if (hexcolor, name) in [(h, n) for h, n in zip(BRAND, BRAND.values())]:
            pass
        if counts.get(rgb, 0) == 0 and name in required:
            missing.append(name)
    note(not missing, f"палитра «{tag}»: фирменные цвета на месте",
         "нет: " + ", ".join(missing) if missing else "")

    # Посторонние цвета: заметная доля (>0.2 %) и не «между» фирменными
    # (антиалиасинг текста — это смесь чернил/бумаги; проверяем близость
    # к любому токену с допуском 24 на канал).
    def near_brand(rgb) -> bool:
        for hexcolor in BRAND:
            b = tuple(int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
            if abs(rgb[0] - b[0]) <= 24 and abs(rgb[1] - b[1]) <= 24 \
                    and abs(rgb[2] - b[2]) <= 24:
                return True
        return False

    strangers = [(rgb, n) for rgb, n in counts.most_common(40)
                 if n > total * 0.002 and not near_brand(rgb)]
    note(not strangers, f"палитра «{tag}»: посторонних доминант нет",
         "; ".join(f"#{r[0]:02X}{r[1]:02X}{r[2]:02X} {100 * n / total:.1f}%"
                   for r, n in strangers[:5]))


def make_long_sample(dst: Path, ffmpeg: Path, seconds: int = 300) -> Path:
    if dst.exists():
        return dst
    subprocess.run(
        [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=160x120:r=10",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "30", "-c:a", "aac", "-shortest", str(dst)],
        check=True, capture_output=True)
    return dst


def pump(root: tk.Tk, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def main() -> int:
    SHOTS.mkdir(exist_ok=True)
    for old in SHOTS.glob("*.png"):
        old.unlink()

    theme.enable_high_dpi()
    root = tk.Tk()

    # Шрифты: семьи реально разрешаются, а не молча подменяются системой
    theme.load_brand_fonts()
    note(tkfont.Font(family="Golos Text").actual("family") == "Golos Text",
         "Golos Text подключён в процесс",
         f"actual={tkfont.Font(family='Golos Text').actual('family')}")
    note(tkfont.Font(family="JetBrains Mono").actual("family")
         == "JetBrains Mono",
         "JetBrains Mono подключён в процесс",
         f"actual={tkfont.Font(family='JetBrains Mono').actual('family')}")

    dialogs: list[str] = []
    gui_mod.messagebox.showerror = lambda t, m, **k: dialogs.append(f"error:{m[:40]}")
    gui_mod.messagebox.showinfo = lambda t, m, **k: dialogs.append(f"info:{m[:40]}")
    gui_mod.messagebox.askyesno = lambda t, m, **k: (dialogs.append(f"yn:{m[:30]}"), False)[1]

    app = ConverterApp(root)
    root.geometry(f"{theme.sc(920)}x{theme.sc(720)}+{theme.sc(80)}+{theme.sc(40)}")
    pump(root, 0.4)
    deadline = time.time() + 10
    while time.time() < deadline:
        root.update()
        if str(app.convert_button["state"]) == "normal":
            break
        time.sleep(0.05)
    note(str(app.convert_button["state"]) == "normal", "кнопка активна после ffmpeg")
    note(not app.install_button.winfo_ismapped(),
         "кнопка «Установить ffmpeg» скрыта при готовом ffmpeg")

    # 1. Пустое окно
    audit_palette("01-пустой", capture(root, "01-пустой"),
                  required=["бумага", "чернила", "графит", "тёмная поверхность",
                            "карточка", "линия", "добавлено на тёмном"])

    # 2. Файлы + все настройки включены
    sample_dir = SHOTS / "файлы"
    sample_dir.mkdir(exist_ok=True)
    for i in range(3):
        app.files.append(sample_dir / f"запись-конференции-{i + 1:02d}.mp4")
        app.marks.append("pending")
    app._redraw()
    app.bitrate_var.set("320")
    app.split_var.set("silence")
    app.normalize_var.set(True)
    app.mono_var.set(True)
    app.denoise_var.set(True)
    app.listbox.selection_set(0)  # выделение — подложка SUBTLE
    pump(root, 0.2)
    audit_palette("02-настройки", capture(root, "02-настройки"),
                  required=["бумага", "чернила", "графит", "тёмная поверхность",
                            "карточка", "линия", "добавлено на тёмном",
                            "удалено на тёмном"])

    # 3. Минимальный размер
    root.geometry("1x1")
    pump(root, 0.2)
    audit_palette("03-минимальный", capture(root, "03-минимальный"),
                  required=["бумага", "тёмная поверхность", "карточка"])

    # 4. Широкое окно
    root.geometry(f"{theme.sc(1320)}x{theme.sc(860)}")
    pump(root, 0.2)
    audit_palette("04-широкий", capture(root, "04-широкий"),
                  required=["бумага", "тёмная поверхность", "карточка"])

    # 5. Конвертация: прогресс зелёный, стоп-кнопка видна
    ffmpeg = ensure_ffmpeg()
    long_mp4 = make_long_sample(SHOTS / "длинный.mp4", ffmpeg)
    app.files.clear()
    app.marks.clear()
    for i in range(3):
        app.files.append(long_mp4)
        app.marks.append("pending")
    app._redraw()
    app.split_var.set("none")
    app.normalize_var.set(False)
    app.mono_var.set(False)
    app.denoise_var.set(False)
    app.output_var.set(str(SHOTS / "out"))
    (SHOTS / "out").mkdir(exist_ok=True)
    app.start_conversion()
    midrun = False
    saw_intra = False   # прогресс шевелился ВНУТРИ файла, а не только между
    saw_pct = False     # статус показывает проценты/время
    deadline = time.time() + 120
    # ждём: первый файл готов (полоса = 1/3, зелёный виден), второй в работе
    while time.time() < deadline:
        root.update()
        frac = app.progress._fraction
        status = app.status_var.get()
        if 0.04 < frac < 0.30:          # внутри первого файла (кластер 1/3)
            saw_intra = True
        if "%" in status or "осталось" in status:
            saw_pct = True
        if app.marks[0] == "ok" and app.converting \
                and str(app.stop_button["state"]) == "normal":
            midrun = True
            break
        if not app.converting:
            break
        time.sleep(0.05)
    shot = capture(root, "05-конвертация")
    note(midrun, "съёмка во время конвертации (стоп-кнопка активна)")
    note(saw_intra, "прогресс двигается внутри файла",
         f"наблюдаемый статус: {app.status_var.get()[:60]}")
    note(saw_pct, "статус показывает проценты и оставшееся время",
         f"статус: {app.status_var.get()[:80]}")
    audit_palette("05-конвертация", shot,
                  required=["бумага", "тёмная поверхность", "карточка",
                            "добавлено", "удалено"])  # зелёный прогресс + красная стоп

    deadline = time.time() + 180
    while time.time() < deadline and app.converting:
        root.update()
        time.sleep(0.05)
    pump(root, 0.2)
    audit_palette("06-итог", capture(root, "06-итог"),
                  required=["бумага", "тёмная поверхность", "карточка"])

    # 6. Окно «О программе»: иконка, ссылки, тексты лицензий
    about = app._show_about()
    pump(root, 0.3)
    audit_palette("07-о-программе", capture(about, "07-о-программе"),
                  required=["бумага", "тёмная поверхность", "карточка",
                            "добавлено на тёмном", "удалено на тёмном"])
    about.destroy()
    pump(root, 0.1)

    print("=" * 70)
    if issues:
        print(f"ПРОБЛЕМ ДИЗАЙНА: {len(issues)}")
        for i in issues:
            print(" -", i)
    else:
        print("ПРОБЛЕМ ДИЗАЙНА НЕ НАЙДЕНО")
    print(f"Кадры: {SHOTS}")
    root.destroy()
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
