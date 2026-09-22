@echo off
REM 课堂助手 一键启动 (Task 46)
REM 双击本文件即可在本机启动本地 HTTP 服务, 并自动打开浏览器。
chcp 65001 >nul 2>&1

setlocal
set "PYTHON=%~dp0..\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

cd /d "%~dp0.."
REM Local AI credentials (if present): scripts\ai-env.bat is gitignored and
REM lives on this machine only. Loaded in the same process before launch
REM (TASK-77), so double-click start needs no manual env setup afterwards.
if exist "%~dp0ai-env.bat" call "%~dp0ai-env.bat"
"%PYTHON%" -m src.application.launcher start %*
exit /b %errorlevel%
