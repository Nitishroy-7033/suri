@echo off
setlocal
cd /d "%~dp0"

set PORT=8080

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3.12 -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
  echo Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)

if not exist ".env" (
  if exist ".env.example" copy /y ".env.example" ".env" >nul
)

rem Free the port first. Usually it is an earlier Jarvis still running, and
rem uvicorn would otherwise start, fail to bind, and quit. Whatever holds the
rem port is named before it is killed, so nothing disappears silently.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ids = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique;" ^
  "foreach ($id in $ids) {" ^
  "  $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$id\";" ^
  "  Write-Host \"Port %PORT% is in use by PID $id ($($p.Name)) - stopping it.\";" ^
  "  if ($p.CommandLine) { Write-Host \"  $($p.CommandLine)\" }" ^
  "  Stop-Process -Id $id -Force -ErrorAction SilentlyContinue" ^
  "}" ^
  "for ($i = 0; $i -lt 20 -and (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 250 }" ^
  "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { Write-Host 'Could not free port %PORT%.'; exit 1 }"
if errorlevel 1 goto :fail

start "" http://127.0.0.1:%PORT%
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT%
goto :eof

:fail
echo.
echo Setup failed. See the error above.
pause
