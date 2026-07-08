@echo off
:: Build pingtester Windows EXE.
:: Requirements: Python 3 installed, pip available.
:: Run this on a Windows machine or in a Windows VM.
:: This script lives in build\ ; sources are in the parent directory.

setlocal
set VERSION=1.0
set SRC=%~dp0..
set DIST=%~dp0dist

if not exist "%DIST%" mkdir "%DIST%"

echo. Installing windows-curses and pyinstaller...
pip install --quiet windows-curses pyinstaller

echo. Building Windows EXE...
pyinstaller ^
    --onefile ^
    --name "pingtester-%VERSION%-windows" ^
    --distpath "%DIST%" ^
    --workpath "%~dp0work\pyinstaller-win" ^
    --specpath "%~dp0work\pyinstaller-win" ^
    --hidden-import report ^
    --hidden-import windows-curses ^
    --log-level WARN ^
    "%SRC%\pingtester.py"

:: report generator is bundled into the EXE (invoked via --generate-report)

echo.
echo Done. EXE in dist\:
dir /b "%DIST%\"
endlocal
