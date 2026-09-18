@echo off
rem ============================================================
rem  MP4 -> MP3 конвертер. Двойной клик по этому файлу = запуск.
rem  Ничего устанавливать не нужно: ffmpeg скачается сам
rem  при первом конвертировании.
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "GUI=%~dp0gui.py"

rem --- 1. Пробуем запустить без чёрного окошка (pythonw) ------
set "QUIET="
for %%C in ("pyw -3" "pythonw") do (
    if not defined QUIET %%~C -c "import sys" >nul 2>nul && set "QUIET=%%~C"
)
if defined QUIET (
    start "" %QUIET% "%GUI%"
    exit /b 0
)

rem --- 2. Обычный python в консоли ----------------------------
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY %%~C -c "import sys" >nul 2>nul && set "PY=%%~C"
)
if not defined PY goto nopython

%PY% "%GUI%"
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo  Python не найден на этом компьютере.
echo.
echo  1. Скачайте Python с открывшейся страницы (кнопка Download).
echo  2. В установщике ОБЯЗАТЕЛЬНО поставьте галочку
echo     "Add python.exe to PATH".
echo  3. Запустите установку, затем снова откройте START.bat.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1
