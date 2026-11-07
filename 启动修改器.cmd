@echo off
chcp 65001 >nul
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw UOEditor.py
    exit /b
)

where python >nul 2>nul
if %errorlevel%==0 (
    python UOEditor.py
    exit /b
)

echo 未找到 Python，请先安装 Python 3（安装时勾选 "Add python.exe to PATH"）。
echo 下载地址: https://www.python.org/downloads/
pause
