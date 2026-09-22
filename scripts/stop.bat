@echo off
REM 课堂助手 停止运行中的实例 (Task 46)
chcp 65001 >nul 2>&1

setlocal
set "PYTHON=%~dp0..\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

cd /d "%~dp0.."
"%PYTHON%" -m src.application.launcher stop %*
exit /b %errorlevel%
