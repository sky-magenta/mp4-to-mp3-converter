"""Точка входа: без аргументов открывает окно (GUI), с аргументами — консоль.

Запуск:  python main.py            → окно конвертера
         python main.py video.mp4  → консольная конвертация
"""

import sys


def main() -> int:
    args = sys.argv[1:]
    if args:
        from converter.cli import main as cli_main
        return cli_main(args)
    try:
        from converter.gui import main as gui_main
    except ImportError:
        print(
            "Не удалось загрузить графику (tkinter). "
            "Используйте консольный режим:\n"
            f"  python {sys.argv[0]} файл.mp4 -o OUT",
            file=sys.stderr,
        )
        return 1
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
