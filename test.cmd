@echo off
REM Windows convenience wrapper so `test.cmd` works from PowerShell and CMD.
REM See start.cmd for why this uses Git Bash explicitly rather than `bash`.
setlocal

set "GITBASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "GITBASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined GITBASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "GITBASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined GITBASH if exist "%LOCALAPPDATA%\Programs\Git\bin\bash.exe" set "GITBASH=%LOCALAPPDATA%\Programs\Git\bin\bash.exe"

if not defined GITBASH (
  echo Could not find Git Bash.
  echo Install Git for Windows ^(https://git-scm.com/download/win^), or run
  echo   ./test.sh
  echo from a Git Bash prompt.
  exit /b 1
)

"%GITBASH%" -lc "cd \"$(cygpath '%~dp0')\" && ./test.sh"
