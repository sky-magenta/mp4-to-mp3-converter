@echo off
rem ============================================================
rem  Сборка одного exe-файла (MP4-to-MP3.exe в папке dist\).
rem  Нужен Python и интернет (ставит PyInstaller).
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
for %%C in ("py -3" "python") do (
    if not defined PY %%~C -c "import sys" >nul 2>nul && set "PY=%%~C"
)
if not defined PY (
    echo Python не найден. Установите с https://www.python.org/downloads/
    pause
    exit /b 1
)

%PY% -m pip install --upgrade pyinstaller || (echo Не удалось установить PyInstaller & pause & exit /b 1)
%PY% -m PyInstaller --noconfirm --onefile --windowed --name "MP4-to-MP3" --icon "assets\icon.ico" --add-data "assets\fonts;assets\fonts" --add-data "assets\icon.ico;assets" --add-data "assets\icon-256.png;assets" --add-data "LICENSE;." --add-data "LICENSE-VISUAL.md;." gui.py || (echo Сборка не удалась & pause & exit /b 1)

echo.
echo Готово: dist\MP4-to-MP3.exe — можно копировать на любой компьютер,
echo Python там уже не нужен. Программа портативная: ffmpeg по кнопке
echo «Установить ffmpeg» в окне, настройки и лог — рядом с exe.
pause
