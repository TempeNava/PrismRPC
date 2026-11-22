@echo off
REM Builds dist\PrismRPC-Setup.exe  (needs Python 3 for Windows from python.org)
REM Keep all .py files, icon.ico and this file together.
cd /d "%~dp0"
py -m pip install --upgrade -r requirements.txt pyinstaller || goto :err

REM 1) the real program, as a folder build (no temp-folder unpacking at runtime)
py -m PyInstaller --clean -y --onedir --noconsole --icon icon.ico --name PrismRPC ^
  --add-data "icon.ico;." --hidden-import pystray._win32 --collect-all customtkinter main.py || goto :err

REM 2) the one-file Setup exe that carries that folder and installs it
py -m PyInstaller --clean -y --onefile --noconsole --icon icon.ico --name PrismRPC-Setup ^
  --add-data "dist\PrismRPC;payload" main.py || goto :err

echo.
echo Done. Send this one file to your friends:  dist\PrismRPC-Setup.exe
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
