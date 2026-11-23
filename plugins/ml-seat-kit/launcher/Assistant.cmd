@echo off
setlocal EnableExtensions
title Assistant
rem Starts your assistant. Run it any time: it installs only what is missing and brings the newest kit. Same steps as
rem Assistant.ps1. Test switches: SEAT_KIT_SOURCE, SEAT_KIT_HOME, SEAT_HOME, SEAT_DESKTOP, SEAT_KIT_DRY_OPEN=1,
rem SEAT_KIT_SIMULATE_MISSING=claude,python,winget (report missing and print the install command, run nothing).
rem Adopt mode puts the kit onto a folder you already use, in place, and installs nothing:
rem   Assistant.cmd adopt --check "<your folder>"   (shows what it would do; writes nothing there)
rem   Assistant.cmd adopt "<your folder>"           (does it; add --name "<name>" to skip the one question)
rem   Assistant.cmd adopt --undo "<your folder>"
set "ADOPT="
set "MODE="
if /i "%~1"=="adopt" set "ADOPT=1"
if defined ADOPT for %%a in (%*) do if /i "%%~a"=="--check" set "MODE=check"
if defined ADOPT for %%a in (%*) do if /i "%%~a"=="--undo" set "MODE=undo"
set "KIT_SOURCE=PUT-KIT-ZIP-ADDRESS-HERE"
if defined SEAT_KIT_SOURCE set "KIT_SOURCE=%SEAT_KIT_SOURCE%"
set "KIT_HOME=%LOCALAPPDATA%\seat-kit"
if defined SEAT_KIT_HOME set "KIT_HOME=%SEAT_KIT_HOME%"
set "SEATS=%USERPROFILE%\Assistant"
if defined SEAT_HOME set "SEATS=%SEAT_HOME%"
set "SIM=,%SEAT_KIT_SIMULATE_MISSING%,"
set "NATIVE=%USERPROFILE%\.local\bin\claude.exe"
set "CLAUDE=%NATIVE%"
set "SYS=%SystemRoot%\System32"
set "WINGET=winget"
set "PYGET=winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements"
set "ASKIT=ask IT to install Python 3.12 for your user from python.org, then double-click this file again."
if not exist "%KIT_HOME%" mkdir "%KIT_HOME%"
<nul set /p "=Step 1 of 6: checking for Claude Code ... "
if not "%SIM:,claude,=%"=="%SIM%" echo missing [simulated], would run: curl -fsSL https://claude.ai/install.cmd -o "%KIT_HOME%\install-claude.cmd" then run it& goto step2
call :findclaude
if defined CFROM echo found (%CFROM%: %CLAUDE%)& goto step2
if defined ADOPT echo not found where the kit looks; adopt does not install it& goto step2
"%SYS%\curl.exe" -fsSL https://claude.ai/install.cmd -o "%KIT_HOME%\install-claude.cmd" && cmd /c ""%KIT_HOME%\install-claude.cmd""
del /q "%KIT_HOME%\install-claude.cmd" 2>nul
set "CLAUDE=%NATIVE%"
if not exist "%CLAUDE%" set "N=1" & set "WHY=Claude Code did not install" & set "TODO=check that you are online, then double-click this file again. If it stops here twice, ask IT to allow the Claude Code installer." & goto stop
echo installed
:step2
<nul set /p "=Step 2 of 6: checking for Python ... "
call :findpy
if not "%SIM:,python,=%"=="%SIM%" echo missing [simulated], would run: %PYGET%& goto simwinget
if defined PY echo found& goto step3
if defined ADOPT goto adoptnopy
where winget >nul 2>nul || set "WINGET=%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe"
if not "%WINGET%"=="winget" if not exist "%WINGET%" set "N=2" & set "WHY=Python is needed and this laptop cannot install it by itself" & set "TODO=%ASKIT%" & goto stop
"%WINGET%" %PYGET:winget =%
call :findpy
if not defined PY set "N=2" & set "WHY=Python did not install" & set "TODO=double-click this file again. If it stops here twice, %ASKIT%" & goto stop
echo installed& goto step3
:simwinget
if not "%SIM:,winget,=%"=="%SIM%" echo [simulated] without winget it would stop here: Python is needed and this laptop cannot install it by itself. What to do: %ASKIT%
if not defined PY set "N=2" & set "WHY=no Python found to continue the simulation" & set "TODO=install Python first" & goto stop
:step3
if not defined MODE call :savepy
for %%i in ("%PY%") do set "PYDIR=%%~dpi"
<nul set /p "=Step 3 of 6: getting the assistant kit ... "
if "%KIT_SOURCE:~0,12%"=="PUT-KIT-ZIP-" goto kitold
if exist "%KIT_HOME%\incoming" rmdir /s /q "%KIT_HOME%\incoming"
mkdir "%KIT_HOME%\incoming"
if exist "%KIT_SOURCE%\.claude-plugin\marketplace.json" xcopy "%KIT_SOURCE%" "%KIT_HOME%\incoming\" /e /i /q /y >nul & goto kitcheck
if /i "%KIT_SOURCE:~0,8%"=="https://" ("%SYS%\curl.exe" -fsL "%KIT_SOURCE%" -o "%KIT_HOME%\kit.zip" || goto kitold) else (copy /y "%KIT_SOURCE%" "%KIT_HOME%\kit.zip" >nul || goto kitold)
"%SYS%\tar.exe" -xf "%KIT_HOME%\kit.zip" -C "%KIT_HOME%\incoming" || goto kitold
:kitcheck
if not exist "%KIT_HOME%\incoming\plugins\ml-seat-kit\scripts\seat_setup.py" goto kitold
if exist "%KIT_HOME%\previous" rmdir /s /q "%KIT_HOME%\previous"
if exist "%KIT_HOME%\current" ren "%KIT_HOME%\current" previous || goto kitold
ren "%KIT_HOME%\incoming" current
if exist "%KIT_HOME%\previous" rmdir /s /q "%KIT_HOME%\previous"
del /q "%KIT_HOME%\kit.zip" 2>nul
echo done& goto step4
:kitold
if exist "%KIT_HOME%\incoming" rmdir /s /q "%KIT_HOME%\incoming"
if not exist "%KIT_HOME%\current\plugins\ml-seat-kit\scripts\seat_setup.py" set "N=3" & set "WHY=could not get the assistant kit" & set "TODO=check that you are online and double-click this file again. If this file came by email or chat, ask the sender for a fresh copy." & goto stop
echo could not get a newer copy, using the one you already have
:step4
if defined ADOPT goto adopt
if /i not "%~f0"=="%KIT_HOME%\Assistant.cmd" copy /y "%~f0" "%KIT_HOME%\Assistant.cmd" >nul
set "ASK="
if /i "%~1"=="setup" set "ASK=--ask"
"%PY%" -I "%KIT_HOME%\current\plugins\ml-seat-kit\scripts\seat_setup.py" --claude "%CLAUDE%" --kit-home "%KIT_HOME%" --seat-home "%SEATS%" --desktop "%SEAT_DESKTOP%" %ASK%
if errorlevel 1 pause & exit /b 1
for /f "usebackq tokens=1* delims=|" %%a in ("%KIT_HOME%\last-seat.txt") do set "SEAT_NAME=%%a" & set "SEAT_DIR=%SEATS%\%%b"
for %%i in ("%CLAUDE%") do set "CLAUDEDIR=%%~dpi"
set "PATH=%PYDIR%;%CLAUDEDIR%;%PATH%"
set "MCP_TIMEOUT=90000"
echo Step 6 of 6: opening your assistant, %SEAT_NAME% ...
if "%SEAT_KIT_DRY_OPEN%"=="1" echo [dry run] would run in "%SEAT_DIR%": "%CLAUDE%" -n "%SEAT_NAME%" "Start my assistant: run the seat-start skill."& exit /b 0
cd /d "%SEAT_DIR%" || (set "N=6" & set "WHY=your assistant's folder is missing" & set "TODO=double-click this file again" & goto stop)
call "%CLAUDE%" -n "%SEAT_NAME%" "Start my assistant: run the seat-start skill."
exit /b 0
:adopt
rem Adopt never opens a session, never copies this file, never makes a shortcut. seat_setup.py does the rest.
"%PY%" -I "%KIT_HOME%\current\plugins\ml-seat-kit\scripts\seat_setup.py" adopt --claude "%CLAUDE%" --kit-home "%KIT_HOME%" --seat-home "%SEATS%" --launcher "%~f0" %2 %3 %4 %5 %6 %7 %8 %9
set "RC=%errorlevel%"
pause
exit /b %RC%
:adoptnopy
echo no Python this kit can use
set "SEEN="
for /f "delims=" %%p in ('where python python3 py 2^>nul') do set "SEEN=1" & echo    found, not used: %%p
if not defined SEEN echo    found: no Python at all
set "N=2" & set "WHY=adopt needs a Python from python.org (a Microsoft Store one is not used) and installs nothing; nothing was changed" & set "TODO=ask IT to install Python 3.12 for your user from python.org, then run this again" & goto stop
:savepy
>"%KIT_HOME%\python.txt" echo(%PY%
exit /b 0
:dropstore
rem Adopt never uses a Microsoft Store Python (under WindowsApps). Only called with PY set, so the edit below is safe.
if not "%PY:WindowsApps=%"=="%PY%" set "PY="
exit /b 0
:findclaude
rem The native install, then an npm install (npm makes claude.cmd, a shim), then anything on PATH. Never a second copy.
set "CFROM="
if exist "%NATIVE%" set "CLAUDE=%NATIVE%" & set "CFROM=native install" & exit /b 0
if exist "%APPDATA%\npm\claude.exe" set "CLAUDE=%APPDATA%\npm\claude.exe" & set "CFROM=npm install" & exit /b 0
if exist "%APPDATA%\npm\claude.cmd" set "CLAUDE=%APPDATA%\npm\claude.cmd" & set "CFROM=npm install" & exit /b 0
for /f "delims=" %%p in ('where claude.exe claude.cmd 2^>nul') do if not defined CFROM set "CLAUDE=%%p" & set "CFROM=on PATH"
exit /b 0
:findpy
set "PY="
if exist "%KIT_HOME%\python.txt" set /p PY=<"%KIT_HOME%\python.txt"
if defined PY if not exist "%PY%" set "PY="
if defined ADOPT if defined PY call :dropstore
if not defined PY for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%d\python.exe" set "PY=%%d\python.exe"
if not defined PY for /f "delims=" %%p in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%p"
if defined ADOPT if defined PY call :dropstore
if not defined PY for /f "delims=" %%p in ('where python 2^>nul ^| findstr /v /i WindowsApps') do if not defined PY set "PY=%%p"
if defined PY "%PY%" -I -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>nul || set "PY="
exit /b 0
:stop
echo.
echo Stopped at step %N%: %WHY%.
echo What to do: %TODO%
pause
exit /b 1
