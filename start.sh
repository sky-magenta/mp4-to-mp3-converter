#!/usr/bin/env bash
# MP4 → MP3 конвертер (macOS / Linux): ./start.sh или двойной клик по start.command
cd "$(dirname "$0")" || exit 1

for PY in python3 python; do
    if command -v "$PY" >/dev/null 2>&1; then
        exec "$PY" gui.py
    fi
done

echo "Python не найден."
echo "macOS:    brew install python"
echo "Ubuntu:   sudo apt install python3 python3-tk ffmpeg"
echo "ffmpeg тоже нужен: brew install ffmpeg / sudo apt install ffmpeg"
exit 1
