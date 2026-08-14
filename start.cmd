@echo off
REM Windows convenience wrapper so `start.cmd` works from PowerShell and CMD.
REM start.sh is a POSIX sh script; running it directly from a Windows shell just
REM opens it in an editor. This finds Git Bash and hands the script over.
REM
REM Deliberately NOT plain `bash` -- on most Windows machines that resolves to
REM WSL's bash, which sees a different filesystem and cannot use .venv\Scripts.
setlocal

set "GITBASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "GITBASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined GITBASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "GITBASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined GITBASH if exist "%LOCALAPPDATA%\Programs\Git\bin\bash.exe" set "GITBASH=%LOCALAPPDATA%\Programs\Git\bin\bash.exe"

if not defined GITBASH (
  echo Could not find Git Bash.
  echo Install Git for Windows ^(https://git-scm.com/download/win^), or run
  echo   ./start.sh
  echo from a Git Bash prompt.
  exit /b 1
)

echo Starting. Press Ctrl+C to stop, then answer Y to "Terminate batch job".
echo Both servers are shut down by port, so nothing is left running either way.
echo.

"%GITBASH%" -lc "cd \"$(cygpath '%~dp0')\" && ./start.sh"

REM Answering Y to CMD's prompt can kill this batch file before the line above
REM returns, so this is a belt-and-braces sweep for the normal-exit case. The
REM real guarantee is start.sh's own port-based cleanup, plus the fact that it
REM clears both ports on startup.
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1
