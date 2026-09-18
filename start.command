#!/usr/bin/env bash
# macOS: двойной клик по этому файлу открывает конвертер.
# Перед первым запуском выполните в Терминале:  chmod +x start.command
cd "$(dirname "$0")" || exit 1
for PY in python3 python; do
    if command -v "$PY" >/dev/null 2>&1; then
        exec "$PY" gui.py
    fi
done
echo "Python не найден: brew install python tkinter ffmpeg"
exit 1
