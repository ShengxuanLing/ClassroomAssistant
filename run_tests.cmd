@echo off
REM 课堂助手 测试运行器 (修复: 使用便携 Python, 跑非集成全套)
chcp 65001 >nul 2>&1

setlocal
set "PYTHON=%~dp0Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"

"%PYTHON%" -m pytest -q -m "not integration" -p no:cacheprovider %*
exit /b %errorlevel%
