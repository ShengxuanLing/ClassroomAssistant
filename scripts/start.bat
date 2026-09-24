@echo off
REM 课堂助手 一键启动 (Task 46)
REM 双击本文件即可在本机启动本地 HTTP 服务, 并自动打开浏览器。
chcp 65001 >nul 2>&1

setlocal
set "PYTHON=%~dp0..\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

cd /d "%~dp0.."
REM Local AI credentials (if present) are loaded by the Python startup chain.
REM Keeping one loader makes start.bat, IDE/debug launch and --check agree on
REM precedence: process environment > .env > scripts\ai-env.bat.
"%PYTHON%" -m src.application.launcher start %*
exit /b %errorlevel%
