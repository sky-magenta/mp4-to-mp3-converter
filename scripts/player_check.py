"""Живая проверка окна предпросмотра: кадры идут, таймлиния рисуется,
метки фрагмента попадают в поля главного окна."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk

import converter.gui as gui_mod
from converter.core import probe_duration
from converter.ffmpeg_setup import find_ffmpeg
from converter.gui import ConverterApp
from converter.player import PlayerWindow, read_ppm
from converter.theme import sc

issues = []


def note(ok, name, detail=""):
    print(f"[{'OK ' if ok else 'BUG'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        issues.append(name)


dialogs = []
gui_mod.messagebox.showinfo = lambda t, m, **k: dialogs.append(m)
gui_mod.messagebox.showerror = lambda t, m, **k: dialogs.append(m)

# короткое видео с картинкой (testsrc — квадраты/градиент, не чёрный кадр)
tmp = Path(tempfile.mkdtemp(prefix="player-check-"))
sample = tmp / "sample.mp4"
ffmpeg = find_ffmpeg()
subprocess.run(
    [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
     "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=4",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
     "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
     "-shortest", str(sample)],
    check=True, capture_output=True)

# 1. парсер PPM на живом конвейере
proc = subprocess.Popen(
    [str(ffmpeg), "-hide_banner", "-loglevel", "error",
     "-i", str(sample), "-vf", f"fps=5,scale={sc(560)}:-2",
     "-f", "image2pipe", "-vcodec", "ppm", "pipe:1"],
    stdout=subprocess.PIPE)
frames = []
while True:
    frame = read_ppm(proc.stdout)
    if frame is None:
        break
    frames.append(frame)
proc.wait()
note(len(frames) >= 15, "кадры читаются из ffmpeg", f"{len(frames)} кадров")
note(all(f.startswith(b"P6") for f in frames), "кадры — валидный P6")

# 2. окно предпросмотра в приложении
root = tk.Tk()
app = ConverterApp(root)
app.files.append(sample)
app.marks.append("pending")
app._redraw()
app.listbox.selection_set(0)
root.update()

deadline = time.time() + 10
while time.time() < deadline:
    root.update()
    if str(app.convert_button["state"]) == "normal":
        break
    time.sleep(0.05)

app.open_player()
player = app._player
note(isinstance(player, PlayerWindow), "плеер открылся")
if player:
    deadline = time.time() + 15
    shot = None
    while time.time() < deadline:
        root.update()
        if player._sized:  # первый кадр показан
            shot = True
            break
        time.sleep(0.05)
    note(shot is True, "первый кадр отрисован",
         f"размер кадра {player.video.winfo_width()}x{player.video.winfo_height()}")

    # играем пару секунд — позиция уходит от нуля
    player.toggle_play()
    deadline = time.time() + 5
    while time.time() < deadline and player.pos < 1.0:
        root.update()
        time.sleep(0.05)
    note(player.pos >= 1.0, "воспроизведение идёт", f"pos={player.pos:.2f}")
    player._set_playing(False)

    # метки фрагмента
    player.pos = 1.2
    player._mark_start()
    player.pos = 2.8
    player._mark_end()
    note(app.start_var.get() == "1.2" and app.end_var.get() == "2.8",
         "метки попали в поля фрагмента",
         f"{app.start_var.get()}–{app.end_var.get()}")

    # таймлиния нарисована
    note(len(player.timeline.find_all()) >= 3, "таймлиния отрисована",
         f"элементов: {len(player.timeline.find_all())}")

    player._close()
    root.update()
    note(app._player is None, "плеер закрыт без следов")

root.destroy()
print("=" * 60)
print("ПРОБЛЕМ:" if issues else "ПЛЕЕР РАБОТАЕТ", len(issues) if issues else "")
for i in issues:
    print(" -", i)
raise SystemExit(1 if issues else 0)
