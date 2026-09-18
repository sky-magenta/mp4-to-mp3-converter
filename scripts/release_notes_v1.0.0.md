# MP4 → MP3 конвертер 1.0.0

Первый публичный релиз. Конвертер видео в MP3 в трёх лицах: портативное
окно, командная строка и **MCP-сервер для управления из ИИ-агентов**.

## Скачать

- **`MP4-to-MP3.exe`** — портативная сборка для Windows: один файл, без
  установки и без Python. ffmpeg, настройки и лог хранятся рядом с exe —
  следов в системе не остаётся; для полного удаления достаточно удалить
  папку. ffmpeg ставится кнопкой «Установить ffmpeg» (~80 МБ, разово).
- Из исходников: `python gui.py` (или `START.bat`); Python 3.9+.

## Возможности

- выравнивание громкости (EBU R128, двухпроходный loudnorm);
- моно для речи и шумоподавление;
- разбивка на дорожки по паузам или фиксированным отрезкам;
- фрагменты, выбор звуковой дорожки, битрейт 96–320, пакетная обработка;
- живой прогресс: проценты внутри файла и оценка оставшегося времени;
- оформление в фирменной теме «бумага и правка» дизайн-системы diff.legal.

## MCP-сервер (для ИИ-агентов)

```bash
pip install "mp4-to-mp3-converter[mcp]"   # или: pip install mcp
```

Конфиг для Claude Desktop и других MCP-клиентов:

```json
{
  "mcpServers": {
    "mp4-to-mp3": {
      "command": "python",
      "args": ["путь/к/mp4-to-mp3-converter/mcp_server.py"]
    }
  }
}
```

Инструменты: `convert` (фоновое задание со всеми опциями), `job_status`
(живой прогресс, оценка остатка, результаты), `job_stop`, `jobs_list`,
`check_ffmpeg`, `install_ffmpeg`, `find_videos`, `probe_media`.

## Лицензии

- **Код** — [MIT](https://github.com/sky-magenta/mp4-to-mp3-converter/blob/main/LICENSE);
- **визуальное оформление** — проприетарная
  [LICENSE-VISUAL.md](https://github.com/sky-magenta/mp4-to-mp3-converter/blob/main/LICENSE-VISUAL.md),
  использование без прямого согласия автора запрещено;
- шрифты Golos Text и JetBrains Mono — SIL OFL 1.1.

---

**Автор: Софья Смирнова** · Telegram: [@forgednotwritten](https://t.me/forgednotwritten) ·
[damascus-ink.ru](https://damascus-ink.ru) — блог о праве, ИИ и LegalTech.
