@echo off
REM Classroom Assistant: backup / restore classroom-data/ (data only; code lives in git)
REM
REM   backup.bat                 -> create baseline backup (manual label)
REM   backup.bat <label>         -> create backup with custom label
REM   backup.bat restore         -> restore the newest backup into classroom-data-restored
REM   backup.bat list            -> list existing backups
REM
REM Data is the single source of truth; code is versioned with git.
setlocal enabledelayedexpansion
set "PYTHON=%~dp0..\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
cd /d "%~dp0.."

if "%~1"=="restore" (
  set "TARGET=%~2"
  if "%TARGET%"=="" set "TARGET=classroom-data-restored"
  "%PYTHON%" -c "import glob,os; from src.backup import restore_backup; z=sorted(glob.glob(os.path.join('classroom-data','backups','backup-*.zip')))[-1]; o=restore_backup(z, '!TARGET!'); print('restored from:', z); print('restored to:', o)" %*
  goto :eof
)
if "%~1"=="list" (
  "%PYTHON%" -c "from src.backup import list_backups; [print(b.path, b.created_at, b.size, 'files='+str(b.file_count)) for b in list_backups('classroom-data')]" %*
  goto :eof
)
"%PYTHON%" -c "import sys; from src.backup import create_backup; o = create_backup('classroom-data', label=(sys.argv[1] if len(sys.argv) > 1 else 'manual')); print('backup:', o)" %*
if errorlevel 1 (echo backup FAILED & pause & exit /b 1)
echo done.
pause
