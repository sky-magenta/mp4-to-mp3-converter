"""Поиск утечек конфиденциального и личного в отслеживаемых файлах.

Проверяет: имена пользователей и локальные пути, личные email, токены
(GitHub, Telegram, AWS, Slack, Anthropic), приватные ключи, пароли в
коде. Выходит с кодом 1, если что-то найдено — используется в CI.

Запуск: python scripts/leak_scan.py [--ref v1.0.0]
По умолчанию проверяется рабочее дерево (HEAD).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (паттерн, описание); именные сущности автора (имя, Telegram, сайт)
# публикуются намеренно — здесь только то, что публиковать нельзя
RULES: list[tuple[str, str]] = [
    (r"(?i)\bsmirn\w*", "имя пользователя ОС"),
    (r"monsterkitty", "личный email"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "токен GitHub"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "токен GitHub (fine-grained)"),
    (r"sk-ant-[A-Za-z0-9\-_]{20,}", "ключ Anthropic"),
    (r"xox[baprs]-[A-Za-z0-9\-]{10,}", "токен Slack"),
    (r"AKIA[0-9A-Z]{16}", "ключ AWS"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "приватный ключ"),
    (r"(?i)\b(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{6,}", "пароль в коде"),
    (r"(?i)(api[_-]?key|secret|token)\s*[=:]\s*['\"][A-Za-z0-9_\-]{20,}",
     "секрет в коде"),
    (r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}", "токен Telegram-бота"),
    (r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
     "email (кроме noreply GitHub)"),
    (r"(?i)c:[\\/]+users[\\/]+\S+", "локальный путь пользователя"),
    (r"(?i)d:[\\/]+coding\b", "путь машины разработки"),
    (r"(?i)/home/[a-z0-9_.-]+/", "локальный путь Linux"),
    (r"(?i)appdata\\+local\\+temp\\+_mei", "путь распаковки PyInstaller"),
    (r"(?i)BtbN\.FFmpeg|winget\.Source", "локальный winget-путь ffmpeg"),
]

COMPILED = [(re.compile(pattern), description) for pattern, description in RULES]

# сам сканер содержит шаблоны правил — на них не срабатываем
SKIP_FILES = {"scripts/leak_scan.py"}

# текстовые файлы сканируем целиком; бинарные ассеты не трогаем
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".txt",
                 ".bat", ".sh", ".command", ".gitignore", ".gitattributes",
                 ".cfg", ".ini", ".spec", ".ps1"}


def tracked_files(ref: str | None) -> list[tuple[str, str]]:
    """[(путь, содержимое)] отслеживаемых текстовых файлов."""
    cmd = ["git", "ls-files"]
    if ref:
        cmd = ["git", "ls-tree", "-r", "--name-only", ref]
    names = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace").stdout.split()
    result = []
    for name in names:
        if name in SKIP_FILES or Path(name).suffix.lower() not in TEXT_SUFFIXES:
            continue
        if ref:
            blob = subprocess.run(["git", "show", f"{ref}:{name}"], cwd=REPO,
                                  capture_output=True).stdout
            result.append((name, blob.decode("utf-8", errors="replace")))
        else:
            path = REPO / name
            if path.is_file():
                result.append((name, path.read_text(encoding="utf-8",
                                                    errors="replace")))
    return result


def main(argv: list[str]) -> int:
    ref = None
    if len(argv) > 1:
        if argv[1] == "--ref" and len(argv) > 2:
            ref = argv[2]
        else:
            print(__doc__)
            return 2

    where = f"коммит {ref}" if ref else "рабочее дерево (HEAD)"
    files = tracked_files(ref)
    findings: list[str] = []
    for name, text in files:
        for regex, description in COMPILED:
            for match in regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                snippet = text[max(0, match.start() - 30):match.end() + 20]
                snippet = snippet.replace("\n", " ").strip()
                findings.append(f"{name}:{line} — {description}: …{snippet}…")

    print(f"Проверено файлов: {len(files)} ({where}), правил: {len(RULES)}")
    if findings:
        print(f"НАЙДЕНО УТЕЧЕК: {len(findings)}")
        for finding in findings:
            print(" -", finding)
        return 1
    print("УТЕЧЕК НЕ НАЙДЕНО")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
