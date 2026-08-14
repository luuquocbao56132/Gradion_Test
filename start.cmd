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

"%GITBASH%" -lc "cd \"$(cygpath '%~dp0')\" && ./start.sh"
