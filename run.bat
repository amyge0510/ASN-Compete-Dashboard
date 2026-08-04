@echo off
rem Double-click me to update the competitor monitor and open the dashboard.
rem First run sets everything up automatically. API key lives in .env.
cd /d "%~dp0"
echo ── Competitor Monitor ──────────────────────────────

if not exist .venv (
  echo First run — setting things up, one-time, about a minute...
  python -m venv .venv
  .venv\Scripts\pip install --quiet -r requirements.txt
)

if exist .env for /f "usebackq tokens=1,* delims==" %%a in (".env") do set %%a=%%b

if "%ANTHROPIC_API_KEY%"=="" (
  echo No API key found. Put it in a file named .env in this folder:
  echo     ANTHROPIC_API_KEY=sk-ant-...
  pause
  exit /b 1
)

echo Checking competitor sites — this takes about 10 minutes. Leave this
echo window open; the dashboard opens by itself when it finishes.
.venv\Scripts\python -m scraper

start site\index.html
echo Done — dashboard opened in your browser.
timeout /t 3 >nul
