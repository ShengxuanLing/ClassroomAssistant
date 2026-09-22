@echo off
REM 课堂助手 健康检查 (Task 46)
REM 装配一遍运行时并打印环境自检与启动快照, 不绑定端口。
chcp 65001 >nul 2>&1

setlocal
set "PYTHON=%~dp0..\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

cd /d "%~dp0.."
"%PYTHON%" -m src.application.launcher health %*
exit /b %errorlevel%
