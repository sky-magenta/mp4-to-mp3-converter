"""Генерация иконки конвертера: знак «зачёркнутая строка + зелёный курсор».

Тёмная плашка #12151C, на ней «волна» из трёх бумажных полос (аудио),
красная линия зачёркивания и зелёный курсор новой строки — фирменная
семантика diff: «удалено → добавлено». Запуск: python scripts/make_icon.py
(нужен Pillow — только для генерации, не для работы конвертера).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

DARK = (18, 21, 28, 255)        # #12151C
PAPER = (232, 231, 225, 255)    # #E8E7E1
REMOVED = (234, 121, 112, 255)  # #EA7970 — на тёмном
ADDED = (76, 175, 126, 255)     # #4CAF7E — на тёмном

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256.0  # все координаты — в системе 256

    # Плашка со скруглением
    d.rounded_rectangle([0, 0, size, size], radius=56 * s, fill=DARK)

    # Аудио-волна: три полосы разной высоты, по центру-слева
    cx = 128
    bar_w = 26 * s
    gap = 20 * s
    heights = [88, 140, 64]
    total_w = 3 * bar_w + 2 * gap
    x0 = cx - total_w / 2 - 18 * s
    for i, h in enumerate(heights):
        bh = h * s
        x = x0 + i * (bar_w + gap)
        d.rounded_rectangle(
            [x, 128 - bh / 2, x + bar_w, 128 + bh / 2],
            radius=bar_w / 2, fill=PAPER,
        )

    # Красная линия зачёркивания — поверх волны
    strike_y = 128
    d.rounded_rectangle(
        [x0 - 10 * s, strike_y - 9 * s, x0 + total_w + 10 * s, strike_y + 9 * s],
        radius=9 * s, fill=REMOVED,
    )

    # Зелёный курсор новой строки — справа от волны
    cur_h = 150 * s
    cur_w = 26 * s
    cx2 = x0 + total_w + 26 * s
    d.rounded_rectangle(
        [cx2, 128 - cur_h / 2, cx2 + cur_w, 128 + cur_h / 2],
        radius=cur_w / 2, fill=ADDED,
    )
    return img


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    base = draw_icon(256)
    base.save(ASSETS / "icon-256.png")
    base.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32),
                                          (48, 48), (64, 64), (128, 128),
                                          (256, 256)])
    print(f"Готово: {ASSETS / 'icon.ico'} и icon-256.png")


if __name__ == "__main__":
    main()
