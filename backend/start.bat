@echo off
REM Hunter Backend — Start Script
REM Kills any existing Python processes on port 8000 and starts fresh.

echo [Hunter] Starting backend...

REM Kill anything on port 8000
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a 2>nul
)
timeout /t 2 /nobreak >nul

REM Activate venv and start uvicorn
call .venv\Scripts\activate.bat
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

pause
