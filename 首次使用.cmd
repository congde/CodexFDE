@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 -m workbench.setup_desktop
  goto done
)
py -3 -c "import sys; sys.exit(not sys.version_info >= (3,10))" >nul 2>nul
if not errorlevel 1 (
  py -3 -X utf8 -m workbench.setup_desktop
  goto done
)
python -c "import sys; sys.exit(not sys.version_info >= (3,10))" >nul 2>nul
if not errorlevel 1 (
  python -X utf8 -m workbench.setup_desktop
  goto done
)
echo 尚未找到 Python 3.10 或更新版本。请按 L00 安装 Python，然后重新双击“首次使用”。
pause
exit /b 1
:done
set "setup_result=%errorlevel%"
pause
exit /b %setup_result%
