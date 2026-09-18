"""Проверка MCP-сервера «из репозитория»: чистая копия (git archive),
запуск с чужой рабочей папкой — ровно так, как запускает его Claude Desktop.

Подготовка копии:  git archive HEAD -o fresh.tar && mkdir fresh && tar -xf fresh.tar -C fresh
Запуск:            python scripts/mcp_fresh_check.py <путь-до-чистой-копии>
Нужен ffmpeg в PATH (или переменная FFMPEG_PATH).
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FRESH = Path(sys.argv[1] if len(sys.argv) > 1 else Path.cwd())
FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"

EXPECTED_TOOLS = {"check_ffmpeg", "install_ffmpeg", "find_videos",
                  "probe_media", "convert", "job_status", "job_stop",
                  "jobs_list"}

failures = []


def note(ok, name, detail=""):
    print(f"[{'OK ' if ok else 'BUG'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        failures.append(name)


async def call(session, name, args=None):
    result = await session.call_tool(name, args or {})
    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent
    text = result.content[0].text if result.content else "{}"
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {"raw": text}


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    assert (FRESH / "mcp_server.py").is_file(), "нет mcp_server.py в копии"
    tmp_out = Path(tempfile.mkdtemp(prefix="mcp-fresh-out-"))
    sample = tmp_out / "sample.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=160x120:r=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(sample)],
        check=True, capture_output=True)

    # cwd специально НЕ папка репозитория: Claude Desktop запускает так
    foreign_cwd = tempfile.mkdtemp(prefix="mcp-foreign-cwd-")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(FRESH / "mcp_server.py")],
        cwd=foreign_cwd,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            note(tools == EXPECTED_TOOLS, "инструменты из чистой копии",
                 ", ".join(sorted(tools)))

            check = await call(session, "check_ffmpeg")
            note(check.get("installed") is True, "check_ffmpeg",
                 str(check.get("path", ""))[:70])

            probe = await call(session, "probe_media", {"path": str(sample)})
            note(probe.get("duration_sec") == 2, "probe_media",
                 f"{probe.get('duration_sec')} с")

            started = await call(session, "convert", {
                "files": [str(sample)],
                "output_dir": str(tmp_out / "mp3"),
                "bitrate": 128,
            })
            note(bool(started.get("job_id")), "convert запущен",
                 str(started)[:90])
            job_id = started.get("job_id", "")

            snap = {}
            deadline = time.time() + 60
            while time.time() < deadline:
                snap = await call(session, "job_status", {"job_id": job_id})
                if snap.get("status") != "running":
                    break
                await asyncio.sleep(0.1)
            dst = snap.get("results", [{}])[0].get("dst")
            ok = (snap.get("status") == "done" and dst
                  and Path(dst).is_file())
            note(bool(ok), "конвертация из чистой копии завершена",
                 f"{snap.get('status')}, {dst}")

    print("=" * 60)
    if failures:
        print("ПРОБЛЕМЫ:", "; ".join(failures))
        return 1
    print("MCP ИЗ РЕПОЗИТОРИЯ РАБОТАЕТ (чужой cwd, чистая копия)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
