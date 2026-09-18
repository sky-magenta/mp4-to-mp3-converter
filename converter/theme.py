"""Фирменная тема «бумага и правка» (дизайн-система diff.legal) для tkinter.

Токены, загрузка шрифтов Golos Text / JetBrains Mono (SIL OFL, файлы в
assets/fonts) и пилюля-кнопка на Canvas. Работает и в собранном exe:
шрифты подгружаются в процесс через AddFontResourceExW(FR_PRIVATE).

ВНИМАНИЕ: визуальное оформление защищено отдельной проприетарной
лицензией — использование без прямого согласия автора запрещено,
см. LICENSE-VISUAL.md. Код конвертера — MIT (LICENSE).
"""

from __future__ import annotations

import ctypes
import math
import sys
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont

# --- токены diff.legal -------------------------------------------------------
PAPER = "#F7F5EF"      # фон
INK = "#171A21"        # текст, основная кнопка
GRAPHITE = "#565B68"   # вторичный текст
LINE = "#DAD8CE"       # разделители
ADDED = "#1E7A4E"      # добавлено / успех
REMOVED = "#C43C30"    # удалено / ошибка
DARK = "#12151C"       # тёмная поверхность (шапка)
SUBTLE = "#ECEBE3"     # подложка
CARD = "#FFFFFF"       # карточка
BORDER = "#CBCBC0"     # рамка
FIELD_BORDER = "#C8CBC1"
INK_HOVER = "#343841"  # наведение основной кнопки
REMOVED_HOVER = "#A93428"

# На тёмной поверхности
ON_DARK_TEXT = "#E8E7E1"
ON_DARK_MUTED = "#9A9FAB"
ON_DARK_ADDED = "#4CAF7E"
ON_DARK_REMOVED = "#EA7970"

_FONT_DIR_CANDIDATES = (
    Path(getattr(sys, "_MEIPASS", "")) / "assets" / "fonts"
    if getattr(sys, "_MEIPASS", "") else None,
    Path(__file__).resolve().parent.parent / "assets" / "fonts",
)

_fonts_ready = False
FAMILY_TEXT = "Segoe UI"      # запасные до загрузки брендовых
FAMILY_MONO = "Consolas"

# Масштаб экранных пикселей: 1.0 на 96 dpi, 1.5 на 144 dpi и т.д.
_SCALE = 1.0


def enable_high_dpi() -> None:
    """Windows: нативный рендер для экранов с масштабированием.

    Без этого Windows растягивает окно битмапой — рваные буквы и «пилы»
    на скруглениях. Вызывать ДО создания главного окна.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def init_scale(root: tk.Misc) -> float:
    """Замеряет фактический DPI после включения информированности."""
    global _SCALE
    _SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    return _SCALE


def sc(px: float) -> int:
    """Логические пиксели → экранные (с учётом масштаба DPI)."""
    return int(round(px * _SCALE))


def scale() -> float:
    return _SCALE


def _brand_font_dir() -> Path | None:
    for candidate in _FONT_DIR_CANDIDATES:
        if candidate and candidate.is_dir() and any(candidate.glob("*.ttf")):
            return candidate
    return None


def load_brand_fonts() -> bool:
    """Подключает брендовые TTF в процесс (Windows). Возвращает успех."""
    global _fonts_ready, FAMILY_TEXT, FAMILY_MONO
    if _fonts_ready:
        return True
    fonts_dir = _brand_font_dir()
    if fonts_dir is None or sys.platform != "win32":
        return False
    try:
        FR_PRIVATE = 0x10
        gdi32 = ctypes.windll.gdi32
        loaded = 0
        for ttf in fonts_dir.glob("*.ttf"):
            if gdi32.AddFontResourceExW(str(ttf), FR_PRIVATE, 0):
                loaded += 1
        if not loaded:
            return False
        FAMILY_TEXT = "Golos Text"
        FAMILY_MONO = "JetBrains Mono"
        _fonts_ready = True
        return True
    except Exception:
        return False


def apply_theme(root: tk.Tk) -> dict:
    """Красит окно и возвращает словарь шрифтов {роль: tkfont.Font}."""
    from tkinter import ttk

    load_brand_fonts()

    fonts = {
        "title": tkfont.Font(family=FAMILY_MONO, size=sc(15), weight="bold"),
        "eyebrow": tkfont.Font(family=FAMILY_MONO, size=sc(8), weight="bold"),
        "text": tkfont.Font(family=FAMILY_TEXT, size=sc(10)),
        "bold": tkfont.Font(family=FAMILY_TEXT, size=sc(10), weight="bold"),
        "button": tkfont.Font(family=FAMILY_TEXT, size=sc(10), weight="bold"),
        "mono": tkfont.Font(family=FAMILY_MONO, size=sc(9)),
        "mono_bold": tkfont.Font(family=FAMILY_MONO, size=sc(9), weight="bold"),
        "status": tkfont.Font(family=FAMILY_TEXT, size=sc(9)),
        "list": tkfont.Font(family=FAMILY_TEXT, size=sc(10)),
        "link": tkfont.Font(family=FAMILY_MONO, size=sc(9), underline=True),
    }

    root.configure(bg=PAPER)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=PAPER, foreground=INK, font=fonts["text"])
    style.configure("TFrame", background=PAPER)
    style.configure("Dark.TFrame", background=DARK)
    style.configure("TLabel", background=PAPER, foreground=INK)
    style.configure("Graphite.TLabel", background=PAPER, foreground=GRAPHITE,
                    font=fonts["status"])
    style.configure("Eyebrow.TLabel", background=PAPER, foreground=GRAPHITE,
                    font=fonts["eyebrow"])
    style.configure("Card.TLabel", background=CARD, foreground=INK)
    style.configure("Dark.TLabel", background=DARK, foreground=ON_DARK_TEXT)
    style.configure("DarkMuted.TLabel", background=DARK,
                    foreground=ON_DARK_MUTED, font=fonts["mono"])

    style.configure("TLabelframe", background=PAPER, bordercolor=LINE,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=PAPER, foreground=GRAPHITE,
                    font=fonts["eyebrow"])

    style.configure("TCheckbutton", background=PAPER, foreground=INK,
                    focuscolor=PAPER, font=fonts["text"])
    style.map("TCheckbutton", background=[("active", PAPER)])
    style.configure("Dark.TCheckbutton", background=DARK, foreground=ON_DARK_TEXT)

    style.configure("TEntry", fieldbackground=CARD, bordercolor=FIELD_BORDER,
                    insertcolor=INK, lightcolor=FIELD_BORDER,
                    darkcolor=FIELD_BORDER, padding=4)
    # фокус поля — зелёный (фокус 2 px #1E7A4E по дизайн-системе)
    style.map("TEntry",
              bordercolor=[("focus", ADDED)],
              lightcolor=[("focus", ADDED)],
              darkcolor=[("focus", ADDED)])
    style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                    bordercolor=FIELD_BORDER, arrowcolor=INK, padding=3)
    style.map("TCombobox",
              fieldbackground=[("readonly", CARD)],
              bordercolor=[("focus", ADDED)])

    # Скроллбар без громоздкой рамки по умолчанию.
    style.configure("Vertical.TScrollbar", background=SUBTLE,
                    troughcolor=PAPER, bordercolor=PAPER, arrowcolor=GRAPHITE)

    return fonts


def _hex_rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


_PILL_CACHE: dict = {}


def _pill_image(w: int, h: int, fill: str, outline: str | None,
                bg: str) -> tk.PhotoImage:
    """Пилюля с настоящим сглаживанием — попиксельно, только stdlib.

    У Canvas нет антиалиасинга: дуги капов выходили ступеньками, а
    контур собирался из дуг и линий со швами. Здесь считаем знак
    расстояния до границы пилюли и смешиваем цвета на краевом пикселе
    (углы «прозрачности» подкрашены цветом подложки). Результат
    кэшируется по размеру и цветам.
    """
    key = (w, h, fill, outline, bg)
    cached = _PILL_CACHE.get(key)
    if cached is not None:
        return cached

    fr, fg, fb = _hex_rgb(fill)
    or_, og, ob = _hex_rgb(outline) if outline else (fr, fg, fb)
    br, bgr_, bb = _hex_rgb(bg)
    r = h / 2                      # пилюля: радиус капа — полвысоты
    straight = max(w / 2 - r, 0.0) # половина прямого участка
    ring = 1.0                     # толщина контура, px

    rows = []
    for y in range(h):
        ady = abs(y + 0.5 - h / 2)
        row = []
        for x in range(w):
            adx = abs(x + 0.5 - w / 2)
            if adx <= straight:
                d = ady - r
            else:
                d = math.hypot(adx - straight, ady) - r
            if d <= -1.5:          # глубоко внутри — без вычислений
                row.append(fill)
                continue
            if d >= 0.5:           # снаружи — цвет подложки
                row.append(bg)
                continue
            a_out = min(max(0.5 - d, 0.0), 1.0)
            a_fill = (min(max(0.5 - (d + ring), 0.0), 1.0)
                      if outline else a_out)
            # подложка → контур → заливка
            c1r, c1g, c1b = (br + (or_ - br) * a_out,
                             bgr_ + (og - bgr_) * a_out,
                             bb + (ob - bb) * a_out)
            row.append(f"#{round(c1r + (fr - c1r) * a_fill):02x}"
                       f"{round(c1g + (fg - c1g) * a_fill):02x}"
                       f"{round(c1b + (fb - c1b) * a_fill):02x}")
        rows.append("{" + " ".join(row) + "}")

    image = tk.PhotoImage(width=w, height=h)
    image.put(" ".join(rows))
    _PILL_CACHE[key] = image
    return image


def _draw_pill(canvas, text, font, fill, text_color, outline=None,
               height=None, padx=18, bg=PAPER):
    """Рисует пилюлю на canvas (используется кнопками и сегментами)."""
    canvas.delete("all")
    w = canvas.winfo_width()
    if w <= 1:
        w = font.measure(text) + sc(padx) * 2
    h = sc(height) if height else sc(36)
    if outline == fill:
        outline = None
    image = _pill_image(w, h, fill, outline, bg)
    canvas.create_image(0, 0, anchor="nw", image=image)
    canvas.create_text(w / 2, h / 2, text=text, fill=text_color, font=font)
    return w


def _canvas_bg(master):
    try:
        bg = master.cget("bg")
        return bg if isinstance(bg, str) and bg.startswith("#") else PAPER
    except Exception:
        return PAPER


class PillButton(tk.Canvas):
    """Кнопка-пилюля из дизайн-системы: плоская, без теней и градиентов.

    kind: primary — чернила с текстом-бумагой; secondary — контур чернилами;
    danger — красный «удалено». Поддерживает config(state=…) как ttk.Button.
    """

    HEIGHT = 36
    SMALL = 30

    def __init__(self, master, text, command=None, kind="primary",
                 padx=20, fonts=None, state="normal", small=False, **kwargs):
        self._font = (fonts or {}).get("button") or tkfont.Font(
            family=FAMILY_TEXT, size=10, weight="bold")
        if small:
            self._font = self._font.copy()
            self._font.configure(size=sc(9))
        self._kind = kind
        self._command = command
        self._state = state
        self._hover = False
        self._text = text
        # храним логическую высоту, в экранные переводит _draw_pill
        self._height = self.SMALL if small else self.HEIGHT

        width = self._font.measure(text) + sc(padx) * 2
        super().__init__(master, width=width, height=sc(self._height),
                         bg=_canvas_bg(master) if isinstance(master, (tk.Frame, tk.Canvas)) else PAPER,
                         highlightthickness=0, **kwargs)
        self._draw()

        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))

    # -- отрисовка ----------------------------------------------------------
    def _palette(self):
        fills = {
            "primary": (INK, PAPER, INK_HOVER),
            "secondary": (PAPER, INK, SUBTLE),
            "danger": (REMOVED, PAPER, REMOVED_HOVER),
        }
        fill, text, hover = fills.get(self._kind, fills["primary"])
        if self._state == "disabled":
            return SUBTLE, GRAPHITE, SUBTLE, LINE
        outline = INK if self._kind == "secondary" else None
        return fill, text, hover, outline

    def _draw(self, text=None):
        if text is not None:
            self._text = text
        fill, text_color, hover_fill, outline = self._palette()
        if self._hover and self._state == "normal":
            fill = hover_fill
        _draw_pill(self, self._text, self._font, fill, text_color,
                   outline=outline, height=self._height,
                   bg=_canvas_bg(self))
        self.configure(cursor="hand2" if self._state == "normal" else "arrow")

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self._draw()

    def _on_click(self, _event) -> None:
        if self._state == "normal" and self._command:
            self._command()

    # -- ttk-подобный API -----------------------------------------------------
    def config(self, cnf=None, **kw):
        if cnf in (None, {}) and not kw:
            return
        state = kw.pop("state", None)
        if state is not None:
            self._state = state
            self._draw()
        if kw:
            super().config(**kw)

    configure = config

    def __getitem__(self, key):
        if key == "state":
            return self._state
        return super().__getitem__(key)


class SegmentedPills(tk.Frame):
    """Переключатель из пилюль: активная залита чернилами, остальные контур.

    Работает с tk.StringVar — старые проверки и логика не меняются.
    """

    HEIGHT = 28

    def __init__(self, master, options, variable, fonts=None, **kwargs):
        # options: список пар (значение, подпись)
        super().__init__(master, bg=_canvas_bg(master), **kwargs)
        self._var = variable
        self._items = list(options)
        font = (fonts or {}).get("mono_bold") or tkfont.Font(
            family=FAMILY_MONO, size=9, weight="bold")
        self._buttons: list[tuple[str, tk.Canvas]] = []
        for value, label in self._items:
            canvas = tk.Canvas(
                self, width=font.measure(label) + sc(24), height=sc(self.HEIGHT),
                bg=_canvas_bg(self), highlightthickness=0, cursor="hand2")
            canvas.pack(side="left", padx=(0, sc(6)))
            canvas.bind("<Button-1>", lambda _e, v=value: self._set(v))
            self._buttons.append((value, canvas))
        self._font = font
        self._var.trace_add("write", lambda *_: self._redraw())
        self._redraw()

    def _set(self, value: str) -> None:
        self._var.set(value)

    def _redraw(self) -> None:
        current = self._var.get()
        for value, canvas in self._buttons:
            if value == current:
                _draw_pill(canvas, dict(self._items)[value], self._font,
                           INK, PAPER, height=self.HEIGHT, padx=12)
            else:
                _draw_pill(canvas, dict(self._items)[value], self._font,
                           PAPER, INK, outline=INK, height=self.HEIGHT, padx=12)


class CheckRow(tk.Canvas):
    """Чекбокс темы: квадрат с чернильной обводкой; включён — залит чернилами.

    Работает с tk.BooleanVar.
    """

    HEIGHT = 26

    def __init__(self, master, text, variable, fonts=None, **kwargs):
        self._var = variable
        self._text = text
        self._font = (fonts or {}).get("text") or tkfont.Font(
            family=FAMILY_TEXT, size=10)
        width = self._font.measure(text) + sc(34)
        super().__init__(master, width=width, height=sc(self.HEIGHT),
                         bg=_canvas_bg(master), highlightthickness=0,
                         cursor="hand2", **kwargs)
        self._var.trace_add("write", lambda *_: self._draw())
        self.bind("<Button-1>", lambda _e: self._var.set(not self._var.get()))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        box, top = sc(16), (sc(self.HEIGHT) - sc(16)) // 2
        edge = max(1, sc(1))  # на high-DPI 1 физический пиксель слишком тонок
        if self._var.get():
            self.create_rectangle(1, top, box + 1, top + box, fill=INK,
                                  outline=INK, width=edge)
            self.create_text(box / 2 + 1, top + box / 2, text="✓",
                             fill=PAPER, font=self._font.copy())
        else:
            self.create_rectangle(1, top, box + 1, top + box, fill=CARD,
                                  outline=INK, width=edge)
        self.create_text(box + sc(12), sc(self.HEIGHT) / 2, text=self._text,
                         fill=INK, anchor="w", font=self._font)


class ThinProgress(tk.Canvas):
    """Тонкая полоса прогресса: трек — линия, заполнение — зелёный «добавлено»."""

    def __init__(self, master, height=4, **kwargs):
        super().__init__(master, height=sc(height), bg=_canvas_bg(master),
                         highlightthickness=0, **kwargs)
        self._fraction = 0.0
        self.bind("<Configure>", lambda _e: self._render())

    def set_fraction(self, fraction: float | None) -> None:
        self._fraction = max(0.0, min(float(fraction or 0.0), 1.0))
        self._render()

    def _render(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w <= 1:
            return
        self.create_rectangle(0, 0, w, self.winfo_height(), fill=LINE,
                              outline="")
        fill_w = int(w * self._fraction)
        if fill_w > 0:
            self.create_rectangle(0, 0, fill_w, self.winfo_height(),
                                  fill=ADDED, outline="")
