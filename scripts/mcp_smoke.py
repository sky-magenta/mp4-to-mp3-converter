"""Живой смоук MCP-сервера: клиент по stdio, реальные вызовы инструментов.

Запуск: python scripts/mcp_smoke.py
Нужен пакет mcp (pip install mcp) и ffmpeg.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = {"check_ffmpeg", "install_ffmpeg", "find_videos",
                  "probe_media", "convert", "job_status", "job_stop",
                  "jobs_list"}


def make_sample(ffmpeg: str, dst: Path) -> Path:
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=160x120:r=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(dst)],
        check=True, capture_output=True)
    return dst


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

    ffmpeg = os.environ.get("FFMPEG_PATH") or "ffmpeg"
    tmp = Path(tempfile.mkdtemp(prefix="mcp-smoke-"))
    sample = make_sample(ffmpeg, tmp / "sample.mp4")

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO / "mcp_server.py")],
    )
    failures = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            print(f"[{'OK' if tools == EXPECTED_TOOLS else 'BUG'}] инструменты: "
                  f"{sorted(tools)}")
            if tools != EXPECTED_TOOLS:
                failures.append("набор инструментов")

            check = await call(session, "check_ffmpeg")
            print(f"[{'OK' if check.get('installed') else 'BUG'}] check_ffmpeg → "
                  f"{check.get('path', '?')}")
            if not check.get("installed"):
                failures.append("ffmpeg не найден")

            probe = await call(session, "probe_media", {"path": str(sample)})
            ok = probe.get("duration_sec") == 2
            print(f"[{'OK' if ok else 'BUG'}] probe_media → {probe}")
            if not ok:
                failures.append("probe_media")

            found = await call(session, "find_videos", {"folder": str(tmp)})
            print(f"[{'OK' if found.get('count') == 1 else 'BUG'}] find_videos → "
                  f"{found.get('count')}")
            if found.get("count") != 1:
                failures.append("find_videos")

            started = await call(session, "convert", {
                "files": [str(sample)],
                "output_dir": str(tmp / "out"),
                "bitrate": 128,
            })
            job_id = started.get("job_id")
            print(f"[{'OK' if job_id else 'BUG'}] convert → job_id={job_id}")
            if not job_id:
                print("   ", started)
                failures.append("convert не запустился")
                raise SystemExit(1)

            # опрос статуса до завершения
            snap = {}
            deadline = time.time() + 60
            while time.time() < deadline:
                snap = await call(session, "job_status", {"job_id": job_id})
                if snap.get("status") != "running":
                    break
                await asyncio.sleep(0.1)
            ok = (snap.get("status") == "done"
                  and snap.get("results", [{}])[0].get("status") == "ok")
            dst = snap.get("results", [{}])[0].get("dst")
            exists = dst and Path(dst).is_file()
            print(f"[{'OK' if ok and exists else 'BUG'}] job_status → "
                  f"{snap.get('status')}, overall={snap.get('overall')}, "
                  f"файл={dst}")
            if not (ok and exists):
                failures.append("задание не завершилось успехом")

            lst = await call(session, "jobs_list")
            n = lst.get("count")
            print(f"[{'OK' if n == 1 else 'BUG'}] jobs_list → "
                  f"{n} задание")

            bad = await call(session, "job_status", {"job_id": "нет-такого"})
            print(f"[{'OK' if 'error' in bad else 'BUG'}] job_status(нет такого) → "
                  f"{bad.get('error', '?')[:40]}")

    print("=" * 60)
    if failures:
        print("СМОУК MCP: ПРОБЛЕМЫ:", "; ".join(failures))
        return 1
    print("СМОУК MCP: ВСЁ РАБОТАЕТ")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
