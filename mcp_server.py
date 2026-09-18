"""Запуск MCP-сервера конвертера: python mcp_server.py

Отдельный файл в корне — чтобы MCP-клиенты (Claude Desktop и др.)
могли указать прямой путь в конфиге без cwd/PYTHONPATH:
"command": "python", "args": ["...\\\\mp4-to-mp3-converter\\\\mcp_server.py"]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from converter.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
