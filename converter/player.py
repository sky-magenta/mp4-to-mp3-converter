"""Встроенный предпросмотр видео: ffmpeg → PPM-кадры → tkinter.

tkinter сам видео не играет, поэтому кадры декодирует ffmpeg
(``-f image2pipe`` в PPM) и отдаёт их в ``PhotoImage`` — парсинг
идёт на C и спокойно тянет 10 fps. Звука нет: для точной резки
достаточно картинки; кнопка «Открыть в плеере» отдаёт файл системному
плееру со звуком. Только стандартная библиотека.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import tkinter as tk

from .core import _track_child_process
from .theme import (ADDED, CARD, DARK, GRAPHITE, INK, LINE, ON_DARK_ADDED,
                    ON_DARK_MUTED, ON_DARK_REMOVED, PAPER, PillButton, sc)
from .util import human_time, parse_time

FPS = 10                 # кадры предпросмотра
PREVIEW_WIDTH = 560      # логическая ширина кадра
SECONDS_STEP = 5


def read_ppm(stream) -> bytes | None:
    """Читает один кадр P6 из потока; None — поток закончился."""
    def readline():
        buf = b""
        while True:
            ch = stream.read(1)
            if not ch:
                return None
            buf += ch
            if ch == b"\n":
                return buf

    magic = readline()
    if magic is None:
        return None
    if not magic.startswith(b"P6"):
        raise ValueError(f"ожидался P6-кадр, получено {magic[:20]!r}")
    size_line = readline()
    while size_line is not None and size_line.startswith(b"#"):
        size_line = readline()
    maxval_line = readline()
    while maxval_line is not None and maxval_line.startswith(b"#"):
        maxval_line = readline()
    if size_line is None or maxval_line is None:
        return None
    width, height = map(int, size_line.split())
    payload = width * height * 3
    data = stream.read(payload)
    if len(data) < payload:
        return None
    return b"P6\n%d %d\n255\n" % (width, height) + data


class PlayerWindow(tk.Toplevel):
    """Окно предпросмотра: просмотр, перемотка, установка границ фрагмента."""

    def __init__(self, master, app, path: Path, ffmpeg: Path,
                 duration: float) -> None:
        super().__init__(master)
        self.app = app
        self.path = Path(path)
        self.ffmpeg = Path(ffmpeg)
        self.duration = float(duration)
        f = app.fonts

        self.title(f"Предпросмотр — {self.path.name}")
        self.configure(bg=PAPER)
        self.transient(master)

        self.pos = 0.0
        self.playing = False
        self._proc: subprocess.Popen | None = None
        self._gen = 0                    # поколение потока (seek убивает старый)
        self._frames: queue.Queue = queue.Queue(maxsize=4)
        self._eof = False
        self._photo: tk.PhotoImage | None = None
        self._sized = False
        self._pending_seek: float | None = None
        pw = sc(PREVIEW_WIDTH)

        # шапка
        header = tk.Frame(self, bg=DARK)
        header.pack(fill="x")
        hi = tk.Frame(header, bg=DARK)
        hi.pack(fill="x", padx=sc(16), pady=sc(8))
        sign = f["title"].copy()
        sign.configure(size=sc(11))
        minus = sign.copy()
        minus.configure(overstrike=True)
        tk.Label(hi, text="−mp4", bg=DARK, fg=ON_DARK_REMOVED,
                 font=minus).pack(side="left")
        tk.Label(hi, text="+mp3", bg=DARK, fg=ON_DARK_ADDED,
                 font=sign).pack(side="left")
        name = self.path.name
        if len(name) > 44:
            name = name[:43] + "…"
        tk.Label(hi, text=name, bg=DARK, fg=ON_DARK_MUTED,
                 font=f["mono"]).pack(side="left", padx=(sc(12), 0))
        tk.Label(hi, text="предпросмотр без звука", bg=DARK,
                 fg=ON_DARK_MUTED, font=f["status"]).pack(side="right")

        body = tk.Frame(self, bg=PAPER)
        body.pack(fill="both", expand=True, padx=sc(16), pady=sc(10))

        # кадр
        video_wrap = tk.Frame(body, bg=LINE)
        video_wrap.pack(fill="x")
        self.video = tk.Canvas(video_wrap, width=pw, height=sc(316),
                               bg=CARD, highlightthickness=0)
        self.video.pack(padx=1, pady=1)
        self.video.create_text(
            pw / 2, sc(316) / 2, text="загружаю первый кадр…", fill=GRAPHITE,
            font=f["status"])

        # таймлиния
        self.timeline = tk.Canvas(body, height=sc(28), bg=PAPER,
                                  highlightthickness=0, cursor="hand2")
        self.timeline.pack(fill="x", pady=(sc(10), 0))
        self.timeline.bind("<Button-1>", self._timeline_press)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)

        # время
        row = tk.Frame(body, bg=PAPER)
        row.pack(fill="x")
        self.time_var = tk.StringVar(value="0:00")
        tk.Label(row, textvariable=self.time_var, bg=PAPER, fg=INK,
                 font=f["mono_bold"]).pack(side="left")
        tk.Label(row, text=f"/ {human_time(self.duration)}", bg=PAPER,
                 fg=GRAPHITE, font=f["mono"]).pack(side="left", padx=(sc(6), 0))

        # управление
        controls = tk.Frame(body, bg=PAPER)
        controls.pack(fill="x", pady=(sc(10), 0))
        self.play_button = PillButton(controls, "Играть", self.toggle_play,
                                      fonts=f)
        self.play_button.pack(side="left")
        PillButton(controls, f"−{SECONDS_STEP} с",
                   lambda: self._skip(-SECONDS_STEP), kind="secondary",
                   small=True, fonts=f).pack(side="left", padx=(sc(8), 0))
        PillButton(controls, f"+{SECONDS_STEP} с",
                   lambda: self._skip(SECONDS_STEP), kind="secondary",
                   small=True, fonts=f).pack(side="left", padx=(sc(8), 0))
        PillButton(controls, "Начало здесь", self._mark_start,
                   kind="secondary", small=True, fonts=f).pack(
            side="right", padx=(sc(8), 0))
        PillButton(controls, "Конец здесь", self._mark_end,
                   kind="secondary", small=True, fonts=f).pack(side="right")

        note_row = tk.Frame(body, bg=PAPER)
        note_row.pack(fill="x", pady=(sc(8), 0))
        link = tk.Label(note_row, text="открыть со звуком в системном плеере",
                        bg=PAPER, fg=INK, cursor="hand2", font=f["link"])
        link.bind("<Button-1>", lambda _e: self._open_external())
        link.pack(side="left")

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())
        self.after(50, self._pump)
        self._seek(0.0, single=True)

    # ------------------------------------------------------------- движение

    def toggle_play(self) -> None:
        if self.playing:
            self._set_playing(False)
        else:
            if self.pos >= self.duration - 0.05:
                self.pos = 0.0
            self._set_playing(True)
            self._start_stream()

    def _set_playing(self, on: bool) -> None:
        self.playing = on
        self.play_button._text = "Пауза" if on else "Играть"
        self.play_button._draw()
        if not on:
            self._stop_stream()
            self._draw_timeline()

    def _skip(self, seconds: float) -> None:
        self._seek(min(max(self.pos + seconds, 0.0), self.duration))

    # --------------------------------------------------------------- поток

    def _start_stream(self) -> None:
        self._stop_stream()
        self._eof = False
        self._drain()
        gen = self._gen = self._gen + 1
        pw = sc(PREVIEW_WIDTH)
        cmd = [
            str(self.ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
            "-ss", f"{max(self.pos - 0.05, 0):.3f}", "-i", str(self.path),
            "-vf", f"fps={FPS},scale={pw}:-2",
            "-f", "image2pipe", "-vcodec", "ppm", "pipe:1",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        _track_child_process(proc)
        self._proc = proc
        single = not self.playing

        def reader():
            try:
                while True:
                    frame = read_ppm(proc.stdout)
                    if frame is None or self._gen != gen:
                        break
                    while True:
                        try:
                            self._frames.put_nowait(frame)
                            break
                        except queue.Full:
                            if self._gen != gen:
                                return
                            time.sleep(0.02)
                    if single:
                        break
            finally:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
                if self._gen == gen:
                    self._eof = True

        threading.Thread(target=reader, daemon=True).start()

    def _stop_stream(self) -> None:
        self._gen += 1
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        self._drain()

    def _drain(self) -> None:
        try:
            while True:
                self._frames.get_nowait()
        except queue.Empty:
            pass

    # ------------------------------------------------------------- перерисовка

    def _pump(self) -> None:
        latest = None
        while True:
            try:
                item = self._frames.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                latest = item
        if latest is not None:
            self._show_frame(latest)
        if self.playing:
            self.pos = min(self.pos + 0.05, self.duration)
            if self._eof or self.pos >= self.duration:
                self.pos = self.duration
                self._set_playing(False)
            elif self._frames.qsize() == 0 and self._proc is None:
                self._start_stream()  # пауза буфера — перезапускаем поток
        self.time_var.set(human_time(self.pos))
        self._draw_timeline()
        self.after(50, self._pump)

    def _show_frame(self, ppm: bytes) -> None:
        photo = tk.PhotoImage(data=ppm, format="ppm", master=self)
        self._photo = photo
        self.video.delete("all")
        self.video.create_image(0, 0, anchor="nw", image=photo)
        if not self._sized:
            self._sized = True
            self.video.configure(height=photo.height())
            self.update_idletasks()

    def _draw_timeline(self) -> None:
        c = self.timeline
        c.delete("all")
        w = max(c.winfo_width(), 2)
        h = sc(28)
        track_y = h // 2
        c.create_rectangle(0, track_y - 1, w, track_y + 1, fill=LINE,
                           outline="")

        def x_of(sec: float) -> float:
            return max(0.0, min(sec / self.duration, 1.0)) * (w - 2) + 1

        start = self._fragment_bound("start")
        end = self._fragment_bound("end")
        if start is not None and end is not None and end > start:
            c.create_rectangle(x_of(start), track_y - sc(3),
                               x_of(end), track_y + sc(3),
                               fill=ADDED, outline="")
        for bound, label in ((start, "A"), (end, "B")):
            if bound is not None:
                x = x_of(bound)
                c.create_line(x, sc(3), x, h - sc(3), fill=INK, width=2)
        for i in range(1, 10):
            x = w * i / 10
            c.create_line(x, track_y + sc(3), x, track_y + sc(6),
                          fill=GRAPHITE)
        x = x_of(self.pos)
        c.create_line(x, sc(2), x, h - sc(2), fill=INK, width=2)

    def _fragment_bound(self, which: str) -> float | None:
        var = self.app.start_var if which == "start" else self.app.end_var
        raw = var.get().strip()
        if not raw:
            return None
        try:
            value = parse_time(raw)
        except Exception:
            return None
        return max(0.0, min(value, self.duration))

    # --------------------------------------------------------------- метки

    def _mark_start(self) -> None:
        self.app.start_var.set(f"{self.pos:.1f}")
        self._draw_timeline()

    def _mark_end(self) -> None:
        self.app.end_var.set(f"{self.pos:.1f}")
        self._draw_timeline()

    # ------------------------------------------------------------ таймлиния

    def _timeline_press(self, event) -> None:
        self._seek(self._event_pos(event))

    def _timeline_drag(self, event) -> None:
        self._pending_seek = self._event_pos(event)
        self.after(140, self._apply_pending_seek)

    def _timeline_release(self, _event) -> None:
        if self._pending_seek is not None:
            pending, self._pending_seek = self._pending_seek, None
            self._seek(pending)

    def _apply_pending_seek(self) -> None:
        if self._pending_seek is not None:
            pending, self._pending_seek = self._pending_seek, None
            self._seek(pending)

    def _event_pos(self, event) -> float:
        w = max(self.timeline.winfo_width(), 2)
        return max(0.0, min(event.x / w, 1.0)) * self.duration

    def _seek(self, seconds: float, single: bool = False) -> None:
        self.pos = max(0.0, min(seconds, self.duration))
        if single:
            was_playing = self.playing
            self._set_playing(False)
            self._start_stream()
            if was_playing:
                self._set_playing(True)
        elif self.playing:
            self._start_stream()
        else:
            self._start_stream()  # один кадр на новой позиции
        self.time_var.set(human_time(self.pos))
        self._draw_timeline()

    # --------------------------------------------------------------- прочее

    def _open_external(self) -> None:
        try:
            os.startfile(self.path)  # Windows-ветка GUI
        except OSError:
            pass

    def _close(self) -> None:
        self._stop_stream()
        player = getattr(self.app, "_player", None)
        if player is self:
            self.app._player = None
        self.destroy()
