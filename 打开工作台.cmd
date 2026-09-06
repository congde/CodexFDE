@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 这是第一次使用或运行环境尚未准备好。请先双击同目录的“首次使用”，完成后再打开工作台。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m workbench.desktop --open-browser
if errorlevel 1 pause
