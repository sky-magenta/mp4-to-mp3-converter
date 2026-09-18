"""Графический интерфейс на tkinter в фирменной теме «бумага и правка».

Весь ffmpeg-скачивание и конвертация идут в фоновом потоке; связь с UI —
через очередь событий, которую опрашивает root.after().
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .core import OK, CANCELLED, convert_batch, probe_duration
from .ffmpeg_setup import cache_bin_dir, data_dir, ensure_ffmpeg, find_ffmpeg
from .player import PlayerWindow
from .theme import (
    ADDED, CARD, DARK, GRAPHITE, INK, LINE, ON_DARK_ADDED, ON_DARK_MUTED,
    ON_DARK_REMOVED, PAPER, REMOVED, CheckRow, PillButton, SegmentedPills,
    ThinProgress, apply_theme, enable_high_dpi, init_scale, sc,
)
from .util import (ConverterError, collect_videos, human_size, human_time,
                   parse_time, ru_plural, validate_bitrate)

_BITRATES = ("96", "128", "160", "192", "256", "320")

SPLIT_NONE = "нет"
SPLIT_SILENCE = "по паузам (авто)"
SPLIT_INTERVAL = "по времени"

_MARK = {"pending": "•", "work": "…", OK: "✓", "skipped": "=", "failed": "✗",
         CANCELLED: "⊘"}
_MARK_COLOR = {"pending": GRAPHITE, "work": GRAPHITE, OK: ADDED,
               "skipped": GRAPHITE, "failed": REMOVED, CANCELLED: GRAPHITE}

_AUTHOR = "Софья Смирнова"


def _resource(*parts: str) -> Path | None:
    """Файл ресурса: сначала распаковка exe, затем корень репозитория."""
    roots = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        candidate = root.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None


_LINKS = (
    ("Telegram: @forgednotwritten", "https://t.me/forgednotwritten"),
    ("damascus-ink.ru", "https://damascus-ink.ru"),
)


def _settings_path() -> Path:
    return data_dir() / "gui-settings.json"


def _migrate_legacy_settings() -> None:
    """Переносим настройки из профиля (старые версии) к exe — один раз."""
    legacy = cache_bin_dir().parent / "gui-settings.json"
    target = _settings_path()
    if legacy != target and not target.is_file() and legacy.is_file():
        try:
            shutil.copy2(legacy, target)
        except OSError:
            pass


class ConverterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MP4 → MP3 конвертер")
        init_scale(root)
        self.fonts = apply_theme(root)

        self.files: list[Path] = []
        self.marks: list[str] = []
        self.events: queue.Queue = queue.Queue()
        self.converting = False
        self.stop_event: threading.Event | None = None
        # счётчики живого прогресса: процент, прошло / осталось
        self._t0 = 0.0
        self._done = 0
        self._normalize_on = False
        self._installing = False
        self._player: PlayerWindow | None = None

        self._build_ui()
        # Минимальный размер — не меньше естественной ширины контента,
        # иначе строки настроек вылезают за окно.
        self.root.update_idletasks()
        self.root.minsize(
            max(sc(760), self.root.winfo_reqwidth() + sc(24)),
            max(sc(580), self.root.winfo_reqheight() + sc(12)),
        )
        self._load_settings()
        self.root.after(80, self._poll_events)
        self.root.after(50, self._prepare_ffmpeg)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        f = self.fonts

        # Иконка окна
        # Иконка окна: iconphoto надёжнее в собранном exe (Tk плохо читает
        # многослойные .ico из каталога распаковки), iconbitmap — запасной путь
        icon_png = _resource("assets", "icon-256.png")
        if icon_png is not None:
            try:
                self._window_icon = tk.PhotoImage(file=str(icon_png))
                self.root.iconphoto(True, self._window_icon)
                print(f"[иконка] iconphoto: {icon_png}", file=sys.stderr)
            except Exception as exc:
                print(f"[иконка] iconphoto не удался: {exc}", file=sys.stderr)
        icon = _resource("assets", "icon.ico")
        if icon is not None:
            try:
                self.root.iconbitmap(str(icon))
            except Exception as exc:
                print(f"[иконка] iconbitmap не удался: {exc}", file=sys.stderr)

        # Шапка: тёмная поверхность с парой «−mp4 +mp3▮»
        header = tk.Frame(self.root, bg=DARK)
        header.pack(fill="x")
        header_inner = tk.Frame(header, bg=DARK)
        header_inner.pack(fill="x", padx=sc(24), pady=sc(16))

        title_font = f["title"].copy()
        title_font.configure(size=sc(17))
        title_box = tk.Frame(header_inner, bg=DARK)
        title_box.pack(side="left")
        minus_font = title_font.copy()
        minus_font.configure(overstrike=True)
        tk.Label(title_box, text="−mp4", bg=DARK, fg=ON_DARK_REMOVED,
                 font=minus_font).pack(side="left")
        tk.Label(title_box, text="+mp3", bg=DARK, fg=ON_DARK_ADDED,
                 font=title_font).pack(side="left")
        # курсор знака: 0,28×0,9 em, опущен к базовой линии (см. дизайн-систему)
        tk.Frame(title_box, bg=ON_DARK_ADDED, width=sc(5),
                 height=sc(15)).pack(side="left", padx=(sc(6), 0),
                                     pady=(sc(3), 0))
        tk.Label(header_inner, text="конвертер · нормализация · разбивка",
                 bg=DARK, fg=ON_DARK_MUTED, font=f["mono"]).pack(
            side="left", padx=(sc(20), 0), pady=(sc(8), 0))

        body = tk.Frame(self.root, bg=PAPER)
        body.pack(fill="both", expand=True, padx=sc(24), pady=sc(14))

        # Список файлов ------------------------------------------------------
        head_row = tk.Frame(body, bg=PAPER)
        head_row.pack(fill="x")
        tk.Label(head_row, text="ВИДЕО ДЛЯ КОНВЕРТАЦИИ", bg=PAPER,
                 fg=GRAPHITE, font=f["eyebrow"]).pack(side="left")
        self.count_var = tk.StringVar(value="")
        tk.Label(head_row, textvariable=self.count_var, bg=PAPER,
                 fg=GRAPHITE, font=f["eyebrow"]).pack(side="right")

        list_wrap = tk.Frame(body, bg=LINE)
        list_wrap.pack(fill="both", expand=True, pady=(sc(6), sc(4)))
        self.listbox = tk.Listbox(
            list_wrap, selectmode=tk.EXTENDED, activestyle="none",
            bg=CARD, fg=INK, relief="flat", borderwidth=0,
            highlightthickness=0, font=f["list"],
            selectbackground=LINE, selectforeground=INK,
        )
        scroll = ttk.Scrollbar(list_wrap, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        scroll.pack(side="right", fill="y")
        self.detail_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.detail_var, bg=PAPER, fg=GRAPHITE,
                 font=f["mono"], anchor="w").pack(fill="x", pady=(0, sc(6)))
        self.listbox.bind("<<ListboxSelect>>", self._show_selected)
        self.listbox.bind("<Double-Button-1>", lambda _e: self.open_player())

        buttons = tk.Frame(body, bg=PAPER)
        buttons.pack(fill="x", pady=(0, sc(12)))
        # главная кнопка экрана одна — «Конвертировать»; остальные — контур
        self._pill(buttons, "Добавить видео…", self.add_files, small=True)
        self._pill(buttons, "Добавить папку…", self.add_folder,
                   kind="secondary", padx_left=8, small=True)
        self._pill(buttons, "Убрать выбранное", self.remove_selected,
                   kind="secondary", padx_left=8, small=True)
        self._pill(buttons, "Очистить", self.clear_files, kind="secondary",
                   padx_left=8, small=True)
        self._pill(buttons, "Смотреть / резать…", self.open_player,
                   kind="secondary", padx_left=8, small=True)

        # Настройки: белая карточка с ровной сеткой ---------------------------
        tk.Label(body, text="НАСТРОЙКИ", bg=PAPER, fg=GRAPHITE,
                 font=f["eyebrow"]).pack(anchor="w")
        panel_wrap = tk.Frame(body, bg=LINE)
        panel_wrap.pack(fill="x", pady=(sc(6), sc(12)))
        panel = tk.Frame(panel_wrap, bg=CARD, padx=sc(16), pady=sc(12))
        panel.pack(fill="x", padx=1, pady=1)

        row1 = tk.Frame(panel, bg=CARD)
        row1.pack(fill="x", pady=sc(4))
        tk.Label(row1, text="Качество", bg=CARD, fg=INK,
                 font=f["text"], width=11, anchor="w").pack(side="left")
        self.bitrate_var = tk.StringVar(value="192")
        SegmentedPills(
            row1, [(v, v) for v in _BITRATES], self.bitrate_var,
            fonts=f).pack(side="left")
        tk.Label(row1, text="kbps", bg=CARD, fg=GRAPHITE,
                 font=f["mono"]).pack(side="left", padx=(2, 0))

        row2 = tk.Frame(panel, bg=CARD)
        row2.pack(fill="x", pady=sc(4))
        tk.Label(row2, text="Разбивка", bg=CARD, fg=INK,
                 font=f["text"], width=11, anchor="w").pack(side="left")
        self.split_var = tk.StringVar(value=SPLIT_NONE)
        SegmentedPills(
            row2,
            [("none", "нет"), ("silence", "по паузам"), ("interval", "по времени")],
            self.split_var, fonts=f,
        ).pack(side="left")
        self._sync_split_display()
        self.split_interval_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.split_interval_var, width=7,
                  font=f["mono"]).pack(side="left", padx=(sc(10), sc(4)))
        tk.Label(row2, text="мин:сек, для «по времени»", bg=CARD,
                 fg=GRAPHITE, font=f["status"]).pack(side="left")

        row3 = tk.Frame(panel, bg=CARD)
        row3.pack(fill="x", pady=sc(4))
        tk.Label(row3, text="Фрагмент", bg=CARD, fg=INK,
                 font=f["text"], width=11, anchor="w").pack(side="left")
        self.start_var = tk.StringVar(value="")
        self.end_var = tk.StringVar(value="")
        ttk.Entry(row3, textvariable=self.start_var, width=7,
                  font=f["mono"]).pack(side="left", padx=(0, 4))
        tk.Label(row3, text="—", bg=CARD, fg=GRAPHITE,
                 font=f["mono"]).pack(side="left")
        ttk.Entry(row3, textvariable=self.end_var, width=7,
                  font=f["mono"]).pack(side="left", padx=4)
        tk.Label(row3, text="напр. 1:30 и 2:05", bg=CARD, fg=GRAPHITE,
                 font=f["status"]).pack(side="left")

        row4 = tk.Frame(panel, bg=CARD)
        row4.pack(fill="x", pady=sc(4))
        tk.Label(row4, text="Папка mp3", bg=CARD, fg=INK,
                 font=f["text"], width=11, anchor="w").pack(side="left")
        self.output_var = tk.StringVar(value="")
        ttk.Entry(row4, textvariable=self.output_var).pack(
            side="left", fill="x", expand=True, padx=(0, sc(8)))
        self._pill(row4, "Обзор…", self.browse_output, kind="secondary",
                   small=True)

        row5 = tk.Frame(panel, bg=CARD)
        row5.pack(fill="x", pady=(sc(8), sc(2)))
        tk.Label(row5, text="Обработка", bg=CARD, fg=INK,
                 font=f["text"], width=11, anchor="w").pack(side="left")
        checks = tk.Frame(row5, bg=CARD)
        checks.pack(side="left")
        self.normalize_var = tk.BooleanVar(value=False)
        self.mono_var = tk.BooleanVar(value=False)
        self.denoise_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.recursive_var = tk.BooleanVar(value=False)
        CheckRow(checks, "выровнять громкость", self.normalize_var,
                 fonts=f).pack(side="left", padx=(0, sc(14)))
        CheckRow(checks, "моно (речь)", self.mono_var,
                 fonts=f).pack(side="left", padx=(0, sc(14)))
        CheckRow(checks, "убрать шум", self.denoise_var,
                 fonts=f).pack(side="left", padx=(0, sc(14)))
        row6 = tk.Frame(panel, bg=CARD)
        row6.pack(fill="x", pady=sc(2))
        tk.Label(row6, text="", bg=CARD, width=11).pack(side="left")
        checks2 = tk.Frame(row6, bg=CARD)
        checks2.pack(side="left")
        CheckRow(checks2, "искать в подпапках", self.recursive_var,
                 fonts=f).pack(side="left", padx=(0, sc(14)))
        CheckRow(checks2, "перезаписывать существующие mp3",
                 self.overwrite_var, fonts=f).pack(side="left")

        # Статус, прогресс и кнопки запуска -----------------------------------
        self.progress = ThinProgress(body)
        self.progress.pack(fill="x", pady=(sc(2), sc(8)))
        bottom = tk.Frame(body, bg=PAPER)
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="Проверяю ffmpeg…")
        tk.Label(bottom, textvariable=self.status_var, bg=PAPER, fg=GRAPHITE,
                 font=f["status"], anchor="w").pack(side="left", fill="x",
                                                    expand=True)
        self.stop_button = self._pill(bottom, "Остановить",
                                      self.stop_conversion, kind="danger",
                                      state="disabled", padx_left=8)
        self.stop_button.pack_forget()  # показываем только во время работы
        self.convert_button = self._pill(bottom, "Конвертировать",
                                         self.start_conversion, state="disabled")
        self.convert_button.pack(side="right")
        # явная установка ffmpeg: показывается, только если его нет
        self.install_button = self._pill(bottom, "Установить ffmpeg",
                                         self.install_dependencies,
                                         kind="secondary", padx_left=8)
        self.install_button.pack_forget()

        # Футер с автором -----------------------------------------------------
        footer = tk.Frame(self.root, bg=PAPER)
        footer.pack(fill="x", padx=sc(24), pady=(0, sc(10)))
        tk.Label(footer, text=f"Автор: {_AUTHOR}", bg=PAPER, fg=GRAPHITE,
                 font=f["status"]).pack(side="left")
        for i, (text, url) in enumerate(_LINKS):
            if i:
                tk.Label(footer, text="·", bg=PAPER, fg=GRAPHITE,
                         font=f["status"]).pack(side="left", padx=sc(6))
            link = tk.Label(footer, text=text, bg=PAPER, fg=INK, cursor="hand2",
                            font=f["link"])
            link.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))
            link.bind("<Enter>", lambda _e, w=link: w.config(fg=ADDED))
            link.bind("<Leave>", lambda _e, w=link: w.config(fg=INK))
            link.pack(side="left", padx=sc(6))
        tk.Label(footer, text="·", bg=PAPER, fg=GRAPHITE,
                 font=f["status"]).pack(side="left", padx=sc(6))
        tk.Label(footer, text="блог о праве, ИИ и LegalTech", bg=PAPER,
                 fg=GRAPHITE, font=f["status"]).pack(side="left")
        tk.Label(footer, text="·", bg=PAPER, fg=GRAPHITE,
                 font=f["status"]).pack(side="left", padx=sc(6))
        license_link = tk.Label(footer, text="Лицензия", bg=PAPER, fg=INK,
                                cursor="hand2", font=f["link"])
        license_link.bind("<Button-1>", lambda _e: self._show_about())
        license_link.bind("<Enter>", lambda _e: license_link.config(fg=ADDED))
        license_link.bind("<Leave>", lambda _e: license_link.config(fg=INK))
        license_link.pack(side="left")

    def _show_about(self) -> tk.Toplevel:
        """Окно «О программе»: иконка, автор, ссылки, полные тексты лицензий."""
        f = self.fonts
        about = tk.Toplevel(self.root)
        about.title("О программе")
        about.configure(bg=PAPER)
        about.transient(self.root)
        about.resizable(False, False)
        icon = _resource("assets", "icon.ico")
        if icon is not None:
            try:
                about.iconbitmap(str(icon))
            except Exception:
                pass

        # тёмная шапка со знаком и версией
        header = tk.Frame(about, bg=DARK)
        header.pack(fill="x")
        hi = tk.Frame(header, bg=DARK)
        hi.pack(fill="x", padx=sc(20), pady=sc(10))
        sign = f["title"].copy()
        sign.configure(size=sc(12))
        minus = sign.copy()
        minus.configure(overstrike=True)
        tk.Label(hi, text="−mp4", bg=DARK, fg=ON_DARK_REMOVED,
                 font=minus).pack(side="left")
        tk.Label(hi, text="+mp3", bg=DARK, fg=ON_DARK_ADDED,
                 font=sign).pack(side="left")
        tk.Frame(hi, bg=ON_DARK_ADDED, width=sc(4),
                 height=sc(11)).pack(side="left", padx=(sc(4), 0),
                                     pady=(sc(2), 0))
        tk.Label(hi, text=f"версия {__version__}", bg=DARK,
                 fg=ON_DARK_MUTED, font=f["mono"]).pack(side="right")

        body = tk.Frame(about, bg=PAPER)
        body.pack(fill="both", expand=True, padx=sc(20), pady=sc(12))

        png = _resource("assets", "icon-256.png")
        if png is not None:
            try:
                img = tk.PhotoImage(file=str(png))
                factor = max(1, round(img.width() / sc(84)))
                if factor > 1:
                    img = img.subsample(factor, factor)
                self._about_icon = img  # удерживаем от сборки мусора
                tk.Label(body, image=img, bg=PAPER).pack(pady=(sc(2), sc(6)))
            except Exception:
                pass

        name = f["bold"].copy()
        name.configure(size=sc(13))
        tk.Label(body, text="MP4 → MP3 конвертер", bg=PAPER, fg=INK,
                 font=name).pack()
        tk.Label(body, text=f"Автор: {_AUTHOR}", bg=PAPER, fg=GRAPHITE,
                 font=f["status"]).pack(pady=(sc(4), 0))

        links = tk.Frame(body, bg=PAPER)
        links.pack(pady=(sc(6), 0))
        for i, (text, url) in enumerate(_LINKS):
            if i:
                tk.Label(links, text="·", bg=PAPER, fg=GRAPHITE,
                         font=f["status"]).pack(side="left", padx=sc(6))
            link = tk.Label(links, text=text, bg=PAPER, fg=INK,
                            cursor="hand2", font=f["link"])
            link.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))
            link.bind("<Enter>", lambda _e, w=link: w.config(fg=ADDED))
            link.pack(side="left", padx=sc(6))
        tk.Label(body, text="портативная программа: файлы — рядом с exe, "
                            "в системе следов нет",
                 bg=PAPER, fg=GRAPHITE, font=f["status"]).pack(pady=(sc(6), 0))

        # полные тексты обеих лицензий
        tk.Label(body, text="ЛИЦЕНЗИИ", bg=PAPER, fg=GRAPHITE,
                 font=f["eyebrow"]).pack(anchor="w", pady=(sc(12), sc(4)))
        mit = _resource("LICENSE")
        visual = _resource("LICENSE-VISUAL.md")
        parts = []
        if mit is not None:
            parts.append(("КОД — MIT", mit.read_text(encoding="utf-8")))
        if visual is not None:
            parts.append(("ВИЗУАЛЬНОЕ ОФОРМЛЕНИЕ — ПРОПРИЕТАРНАЯ ЛИЦЕНЗИЯ",
                          visual.read_text(encoding="utf-8")))
        if not parts:
            parts = [("", "Код — MIT (см. LICENSE). Визуальное оформление — "
                          "проприетарная лицензия (см. LICENSE-VISUAL.md).")]
        wrap_frame = tk.Frame(body, bg=LINE)
        wrap_frame.pack(fill="x")
        text_widget = tk.Text(
            wrap_frame, bg=CARD, fg=INK, relief="flat", wrap="word",
            font=f["mono"], width=64, height=12, padx=sc(10), pady=sc(8),
            highlightthickness=0, cursor="arrow",
        )
        for title, body_text in parts:
            text_widget.insert("end", f"— {title} —\n\n" if title else "")
            text_widget.insert("end", body_text.rstrip() + "\n\n")
        text_widget.configure(state="disabled")
        scroll = ttk.Scrollbar(wrap_frame, command=text_widget.yview)
        text_widget.config(yscrollcommand=scroll.set)
        text_widget.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        scroll.pack(side="right", fill="y")

        PillButton(body, "Закрыть", about.destroy, fonts=f).pack(pady=sc(12))

        # по центру родительского окна
        about.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width()
                                       - about.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height()
                                       - about.winfo_height()) // 2
        about.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        about.grab_set()
        about.bind("<Escape>", lambda _e: about.destroy())
        about.focus_set()
        return about

    def _sync_split_display(self) -> None:
        """Значения сегмента «Разбивка» ↔ текстовые константы настроек."""
        mapping = {"none": SPLIT_NONE, "silence": SPLIT_SILENCE,
                   "interval": SPLIT_INTERVAL}
        reverse = {v: k for k, v in mapping.items()}
        self._split_map = mapping
        self._split_reverse = reverse

    def _pill(self, parent, text, command, kind="primary", padx_left=0,
              state="normal", small=False):
        button = PillButton(parent, text, command, kind=kind,
                            fonts=self.fonts, state=state, small=small)
        button.pack(side="left", padx=(sc(padx_left), 0))
        return button

    # ------------------------------------------------------------ настройки

    def _load_settings(self) -> None:
        _migrate_legacy_settings()
        try:
            data = json.loads(_settings_path().read_text(encoding="utf-8"))
            self.output_var.set(data.get("output", ""))
            if str(data.get("bitrate", "")) in _BITRATES:
                self.bitrate_var.set(str(data["bitrate"]))
            self.overwrite_var.set(bool(data.get("overwrite", False)))
            self.normalize_var.set(bool(data.get("normalize", False)))
            self.mono_var.set(bool(data.get("mono", False)))
            self.denoise_var.set(bool(data.get("denoise", False)))
            self.recursive_var.set(bool(data.get("recursive", False)))
            # раньше режим разбивки хранился текстом — переводим в ключ сегмента
            stored_split = self._split_reverse.get(
                data.get("split"), data.get("split", "none"))
            if stored_split in ("none", "silence", "interval"):
                self.split_var.set(stored_split)
            self.split_interval_var.set(data.get("split_interval", ""))
        except (OSError, ValueError):
            pass

    def _save_settings(self) -> None:
        try:
            _settings_path().parent.mkdir(parents=True, exist_ok=True)
            _settings_path().write_text(
                json.dumps(
                    {
                        "output": self.output_var.get(),
                        "bitrate": self.bitrate_var.get(),
                        "overwrite": self.overwrite_var.get(),
                        "normalize": self.normalize_var.get(),
                        "mono": self.mono_var.get(),
                        "denoise": self.denoise_var.get(),
                        "recursive": self.recursive_var.get(),
                        "split": self.split_var.get(),
                        "split_interval": self.split_interval_var.get(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ------------------------------------------------------------- список

    def _redraw(self) -> None:
        self.listbox.delete(0, tk.END)
        for i, path in enumerate(self.files):
            mark = _MARK.get(self.marks[i], "•")
            self.listbox.insert(tk.END, f" {mark}  {path.name}")
            self.listbox.itemconfigure(i, foreground=_MARK_COLOR.get(
                self.marks[i], INK))
        self._update_count()
        self.detail_var.set("")

    def _update_count(self) -> None:
        n = len(self.files)
        if not n:
            self.count_var.set("")
        else:
            self.count_var.set(f"{n} {ru_plural(n, 'файл', 'файла', 'файлов')}")

    def _show_selected(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if selection:
            self.detail_var.set(str(self.files[selection[0]]))
        else:
            self.detail_var.set("")

    def open_player(self) -> None:
        """Предпросмотр выбранного видео: просмотр и разметка фрагмента."""
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo(
                "MP4 → MP3",
                "Выберите видео в списке — его можно посмотреть и разметить "
                "фрагмент.")
            return
        path = self.files[selection[0]]
        ffmpeg = find_ffmpeg()
        if ffmpeg is None:
            messagebox.showinfo(
                "MP4 → MP3",
                "Сначала установите ffmpeg — кнопка «Установить ffmpeg».")
            return
        duration = probe_duration(ffmpeg, path)
        if not duration:
            messagebox.showerror(
                "MP4 → MP3", "Не удалось определить длительность файла.")
            return
        self._player = PlayerWindow(self.root, self, path, ffmpeg, duration)

    def _add_paths(self, paths) -> None:
        try:
            new = collect_videos(paths, recursive=self.recursive_var.get())
        except ConverterError as exc:
            messagebox.showerror("MP4 → MP3", str(exc))
            return
        for path in new:
            if path not in self.files:
                self.files.append(path)
                self.marks.append("pending")
        self._redraw()

    def add_files(self) -> None:
        names = filedialog.askopenfilenames(
            title="Выберите видео",
            filetypes=[("Видео", "*.mp4 *.m4v *.mov *.mkv *.avi *.webm *.ts *.wmv *.flv"),
                       ("Все файлы", "*.*")],
        )
        self._add_paths(map(Path, names))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Выберите папку с видео")
        if folder:
            self._add_paths([Path(folder)])

    def remove_selected(self) -> None:
        for index in sorted(self.listbox.curselection(), reverse=True):
            del self.files[index]
            del self.marks[index]
        self._redraw()

    def clear_files(self) -> None:
        self.files.clear()
        self.marks.clear()
        self._redraw()

    def browse_output(self) -> None:
        folder = filedialog.askdirectory(title="Куда сохранять mp3")
        if folder:
            self.output_var.set(folder)

    # --------------------------------------------------------------- ffmpeg

    def _prepare_ffmpeg(self) -> None:
        """При старте только проверяем; скачивание — по явной кнопке."""

        def worker():
            try:
                found = find_ffmpeg()
            except Exception:
                self.events.put(("ffmpeg_error", traceback.format_exc()))
                return
            if found:
                self.events.put(
                    ("ffmpeg_ready", "ffmpeg готов — можно конвертировать"))
            else:
                self.events.put(("ffmpeg_missing",))

        threading.Thread(target=worker, daemon=True).start()

    def install_dependencies(self) -> None:
        """Кнопка «Установить ffmpeg»: скачивание в папку рядом с exe."""
        if self._installing:
            return
        self._installing = True
        self.install_button.config(state="disabled")
        self.status_var.set("Скачиваю ffmpeg (одноразово, ~80 МБ)…")
        self.progress.set_fraction(0)

        def worker():
            try:
                ensure_ffmpeg(progress=self._on_download)
                self.events.put(
                    ("ffmpeg_ready", "ffmpeg установлен — можно конвертировать"))
            except ConverterError as exc:
                self.events.put(("ffmpeg_error", str(exc)))
            except Exception:
                self.events.put(("ffmpeg_error", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()

    def _on_download(self, received: int, total: int | None) -> None:
        self.events.put(("download", received, total))

    # ------------------------------------------------------------ конвертация

    def start_conversion(self) -> None:
        if self.converting:
            return
        if not self.files:
            messagebox.showinfo("MP4 → MP3", "Сначала добавьте видео-файлы.")
            return
        try:
            bitrate = validate_bitrate(self.bitrate_var.get())
            start = parse_time(self.start_var.get()) if self.start_var.get().strip() else None
            end = parse_time(self.end_var.get()) if self.end_var.get().strip() else None
            split_mode = None
            split_interval = None
            split_key = self._split_reverse.get(self.split_var.get(),
                                                self.split_var.get())
            if split_key == "silence":
                split_mode = "silence"
            elif split_key == "interval":
                split_mode = "interval"
                if not self.split_interval_var.get().strip():
                    raise ConverterError("Укажите интервал разбивки, например 10:00")
                split_interval = parse_time(self.split_interval_var.get())
        except ConverterError as exc:
            messagebox.showerror("MP4 → MP3", str(exc))
            return
        if start is not None and end is not None and end <= start:
            messagebox.showerror(
                "MP4 → MP3", "Конец фрагмента должен быть позже начала.")
            return

        output_dir = self.output_var.get().strip()
        dst_dir = Path(output_dir) if output_dir else None
        if dst_dir is not None and not dst_dir.is_dir():
            if not messagebox.askyesno(
                "MP4 → MP3",
                f"Папки «{dst_dir}» не существует.\nСоздать её?",
            ):
                return

        overwrite = "overwrite" if self.overwrite_var.get() else "rename"
        files = list(self.files)
        # Снимаем значения tk-переменных ЗДЕСЬ, в главном потоке: читать их
        # из воркера нельзя — tkinter не потокобезопасен
        # (RuntimeError: main thread is not in main loop).
        snapshot = {
            "normalize": self.normalize_var.get(),
            "mono": self.mono_var.get(),
            "denoise": self.denoise_var.get(),
        }
        self.stop_event = threading.Event()
        self.converting = True
        self.convert_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.stop_button.pack(side="right", padx=(sc(8), 0), before=self.convert_button)
        self.progress.set_fraction(0)
        self._save_settings()
        for i in range(len(self.marks)):
            self.marks[i] = "pending"
        self._redraw()

        # счётчики живого прогресса: процент, прошло / осталось
        self._t0 = time.monotonic()
        self._done = 0
        self._normalize_on = snapshot["normalize"]
        self.status_var.set(f"Конвертирую 1 из {len(files)}…")

        def on_result(result, index, total):
            self.events.put(("result", index - 1, result.status,
                             result.message, str(result.dst or ""),
                             result.size_bytes))

        def on_progress(index, name, fraction):
            self.events.put(("progress", index, name, fraction))

        def worker():
            try:
                summary = convert_batch(
                    files,
                    dst_dir=dst_dir,
                    bitrate=bitrate,
                    overwrite=overwrite,
                    on_result=on_result,
                    on_progress=on_progress,
                    start=start,
                    end=end,
                    normalize=snapshot["normalize"],
                    mono=snapshot["mono"],
                    denoise=snapshot["denoise"],
                    stop=self.stop_event,
                    split_mode=split_mode,
                    split_interval=split_interval,
                )
                self.events.put(("done", str(summary), summary))
            except ConverterError as exc:
                self.events.put(("fatal", str(exc)))
            except Exception:
                self.events.put(("fatal", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()

    def stop_conversion(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
            self.status_var.set("Останавливаю (текущий файл будет прерван)…")
            self.stop_button.config(state="disabled")

    # ------------------------------------------------------------- события

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_events)

    def _handle_event(self, event) -> None:
        kind = event[0]
        if kind == "status":
            self.status_var.set(event[1])
        elif kind == "ffmpeg_ready":
            self.status_var.set(event[1])
            self.convert_button.config(state="normal")
            self._installing = False
            self.install_button.config(state="normal")
            self.install_button.pack_forget()
            self.progress.set_fraction(0)
        elif kind == "ffmpeg_missing":
            self.status_var.set(
                "ffmpeg не установлен — нажмите «Установить ffmpeg»")
            self.install_button.pack(side="right", padx=(sc(8), 0))
        elif kind == "download":
            _, received, total = event
            if total:
                fraction = min(received / total, 1.0)
                self.progress.set_fraction(fraction)
                self.status_var.set(
                    f"Скачиваю ffmpeg… {fraction * 100:.0f}% "
                    f"({human_size(received)} из {human_size(total)})")
            else:
                self.status_var.set(
                    f"Скачиваю ffmpeg… {human_size(received)}")
        elif kind == "ffmpeg_error":
            self._installing = False
            if not self.install_button.winfo_ismapped():
                self.install_button.pack(side="right", padx=(sc(8), 0))
            self.install_button.config(state="normal")
            self.status_var.set("ffmpeg не найден")
            messagebox.showerror("MP4 → MP3 — нужен ffmpeg", event[1])
        elif kind == "result":
            _, index, status, message, dst, size = event
            self.marks[index] = status
            self._done = max(self._done, index + 1)
            name = self.files[index].name
            extras = []
            if status == OK and size:
                extras.append(f"{human_size(size)}")
            elif message and message != "готово":
                extras.append(message)
            suffix = f" — {', '.join(extras)}" if extras else ""
            self.listbox.delete(index)
            self.listbox.insert(
                index, f" {_MARK.get(status, '•')}  {name}{suffix}")
            self.listbox.itemconfigure(index, foreground=_MARK_COLOR.get(status, INK))
            self.progress.set_fraction(self._done / len(self.files))
        elif kind == "progress":
            _, _index, name, fraction = event
            total = len(self.files)
            fraction = min(max(fraction, 0.0), 1.0)
            overall = (self._done + fraction) / total
            self.progress.set_fraction(overall)
            elapsed = time.monotonic() - self._t0
            current = min(self._done + 1, total)
            short = name if len(name) <= 30 else name[:29] + "…"
            if fraction > 0.001:
                text = f"{current}/{total} · {short} — {fraction * 100:.0f}%"
            elif self._normalize_on:
                text = f"{current}/{total} · {short} — замер громкости…"
            else:
                text = f"{current}/{total} · {short} — запуск…"
            if overall > 0.02:  # раньше оценки слишком врут
                text += f" · осталось ~{human_time(elapsed * (1 - overall) / overall)}"
            self.status_var.set(text)
        elif kind == "done":
            self.converting = False
            self.convert_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.stop_button.pack_forget()
            self.progress.set_fraction(1 if event[2].ok else 0)
            summary = event[2]
            text = str(summary)
            if summary.ok:
                text += (f"\nСоздано mp3: {len(summary.ok)} шт., "
                         f"{human_size(summary.total_size)} "
                         f"за {human_time(summary.elapsed_sec)}")
            self.status_var.set(text.splitlines()[0])
            if summary.ok:
                if messagebox.askyesno("MP4 → MP3",
                                       f"{text}\n\nОткрыть папку с результатами?"):
                    self._open_output(summary)
        elif kind == "fatal":
            self.converting = False
            self.convert_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.stop_button.pack_forget()
            self.status_var.set("Ошибка")
            messagebox.showerror("MP4 → MP3", event[1])

    def _open_output(self, summary) -> None:
        target = None
        if self.output_var.get().strip():
            target = Path(self.output_var.get().strip())
        elif summary.ok and summary.ok[0].dst:
            target = summary.ok[0].dst.parent
        if target and target.is_dir():
            os.startfile(target)  # Windows-only ветка GUI


def main() -> int:
    enable_high_dpi()
    root = tk.Tk()
    app = ConverterApp(root)

    # В оконном exe stderr может отсутствовать — штатный обработчик
    # tkinter тогда падает и убивает процесс. Пишем трейсбек в лог сами.
    def _callback_exception(exc_type, exc, tb):
        try:
            from converter.gui import _settings_path
            log = _settings_path().parent / "mp4tomp3.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass

    root.report_callback_exception = _callback_exception
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
