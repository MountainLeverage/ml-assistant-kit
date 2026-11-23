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
rem Self test mode only runs the kit's self tests and says what it found (installs nothing, sets nothing up):
rem   Assistant.cmd selftest
set "ADOPT="
set "SELFTEST="
set "NOINST="
set "MODE="
if /i "%~1"=="adopt" set "ADOPT=1" & set "NOINST=adopt"
if /i "%~1"=="selftest" set "SELFTEST=1" & set "NOINST=selftest" & set "MODE=selftest"
if defined ADOPT for %%a in (%*) do if /i "%%~a"=="--check" set "MODE=check"
if defined ADOPT for %%a in (%*) do if /i "%%~a"=="--undo" set "MODE=undo"
set "KIT_SOURCE=https://github.com/MountainLeverage/ml-assistant-kit/releases/latest/download/seat-kit.zip"
set "KIT_NEED=0.1.4"
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
if defined NOINST echo not found where the kit looks; %NOINST% does not install it& goto step2
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
if defined NOINST goto nopy
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
rem Step 3 never renames, overwrites or deletes anything that may be in use. The download gets a new file name
rem each time; the zip puts itself into kits\<version>, a folder that did not exist a moment before, then switches
rem kit.txt to it (see kit_place.py). The old folder "current" (kit 0.1.3 and older) is left exactly as it is.
rem Self test and adopt never run a kit older than KIT_NEED, and never an older kit in place of the newest.
<nul set /p "=Step 3 of 6: getting the assistant kit ... "
set "RC3=" & set "WHYKIT=" & set "KITDIR=" & set "KV=" & set "KOK="
set "TOK=%RANDOM%%RANDOM%"
set "ERRF=%KIT_HOME%\dl-%TOK%.txt"
if "%KIT_SOURCE:~0,12%"=="PUT-KIT-ZIP-" set "WHYKIT=This launcher has no download address." & goto kitnot
if exist "%KIT_SOURCE%\.claude-plugin\marketplace.json" goto kitfolder
if /i not "%KIT_SOURCE:~0,8%"=="https://" goto kitfile
set "DLZIP=%KIT_HOME%\dl-%TOK%.zip"
"%SYS%\curl.exe" -fsSL --retry 2 --retry-delay 1 --stderr "%ERRF%" "%KIT_SOURCE%" -o "%DLZIP%" || set "WHYKIT=The download did not work."
if defined WHYKIT del /q "%DLZIP%" 2>nul & goto kitnot
"%PY%" -I "%DLZIP%" --kit-home "%KIT_HOME%" 2>nul
set "RC3=%errorlevel%"
del /q "%DLZIP%" 2>nul
goto kitafter
:kitfile
if not exist "%KIT_SOURCE%" set "WHYKIT=The kit file %KIT_SOURCE% is not there." & goto kitnot
"%PY%" -I "%KIT_SOURCE%" --kit-home "%KIT_HOME%" 2>nul
set "RC3=%errorlevel%"
goto kitafter
:kitfolder
"%PY%" -I "%KIT_SOURCE%\plugins\ml-seat-kit\scripts\kit_place.py" --kit-home "%KIT_HOME%" --folder "%KIT_SOURCE%" 2>nul
set "RC3=%errorlevel%"
:kitafter
if "%RC3%"=="0" goto kitpick
if not "%RC3%"=="3" echo not done& echo    The kit that was fetched could not put itself in place (a kit older than 0.1.4 cannot).
echo    The kit folder is %KIT_HOME%
goto kitpick
:kitnot
echo not done
echo    %WHYKIT%
if exist "%ERRF%" for /f "usebackq delims=" %%l in ("%ERRF%") do echo    %%l
echo    The kit folder is %KIT_HOME%
:kitpick
del /q "%ERRF%" 2>nul
set "ACTIVE="
if exist "%KIT_HOME%\kit.txt" set /p ACTIVE=<"%KIT_HOME%\kit.txt"
if defined ACTIVE if exist "%KIT_HOME%\kits\%ACTIVE%\plugins\ml-seat-kit\scripts\seat_setup.py" set "KITDIR=%KIT_HOME%\kits\%ACTIVE%"
if not "%RC3%"=="0" if not exist "%KIT_HOME%\kit.txt" if exist "%KIT_HOME%\current\plugins\ml-seat-kit\scripts\seat_setup.py" set "KITDIR=%KIT_HOME%\current"
if defined KITDIR for /f "usebackq tokens=1,2" %%a in (`call "%PY%" -I -c "import json,re,sys;v=json.load(open(sys.argv[1],encoding='utf-8'))['version'];t=lambda s:[int(x) for x in re.findall('[0-9]+',s)[:3]];print(v,int(max(t(v),t(sys.argv[2]))==t(v)))" "%KITDIR%\plugins\ml-seat-kit\.claude-plugin\plugin.json" %KIT_NEED% 2^>nul`) do set "KV=%%a" & set "KOK=%%b"
if not defined KV set "KV=unknown"
if "%RC3%"=="0" if not defined KITDIR set "N=3" & set "WHY=the new kit was put in place but kit.txt does not name it, so nothing was run" & set "TODO=check that you are online, close any window or program using the kit folder named above, and run this again. If it stops here twice, send this window to whoever set up your assistant." & goto stop
if not defined NOINST goto kitnormal
set "TODO=check that you are online, close any window or program using the kit folder named above, and run this again. If it stops here twice, send this window to whoever set up your assistant."
if not "%RC3%"=="0" if defined KITDIR set "N=3" & set "WHY=could not get kit %KIT_NEED%, and the kit already on this PC (%KV%) is never used for %NOINST%, so nothing was run" & goto stop
if not "%RC3%"=="0" set "N=3" & set "WHY=could not get kit %KIT_NEED%, so nothing was run" & goto stop
if not "%KOK%"=="1" set "N=3" & set "WHY=the kit that was put in place is %KV%, and %NOINST% needs kit %KIT_NEED% or newer, so nothing was run" & goto stop
goto kitline
:kitnormal
rem A normal start uses the kit already here when no newer one comes: being offline must not stop the assistant.
if not "%RC3%"=="0" if exist "%KIT_HOME%\kit.txt" if not defined KITDIR set "N=3" & set "WHY=kit.txt names a kit folder that is not there" & set "TODO=check that you are online and double-click this file again. If it stops here twice, send this window to whoever set up your assistant." & goto stop
if not defined KITDIR set "N=3" & set "WHY=could not get the assistant kit" & set "TODO=check that you are online and double-click this file again. If this file came by email or chat, ask the sender for a fresh copy." & goto stop
if not "%RC3%"=="0" echo    Using the kit already on this laptop.
:kitline
echo Kit in use: %KV% (%KITDIR%)
:step4
if defined ADOPT goto adopt
if defined SELFTEST goto selftest
if /i not "%~f0"=="%KIT_HOME%\Assistant.cmd" copy /y "%~f0" "%KIT_HOME%\Assistant.cmd" >nul
set "ASK="
if /i "%~1"=="setup" set "ASK=--ask"
"%PY%" -I "%KITDIR%\plugins\ml-seat-kit\scripts\seat_setup.py" --claude "%CLAUDE%" --kit-home "%KIT_HOME%" --seat-home "%SEATS%" --desktop "%SEAT_DESKTOP%" %ASK%
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
"%PY%" -I "%KITDIR%\plugins\ml-seat-kit\scripts\seat_setup.py" adopt --claude "%CLAUDE%" --kit-home "%KIT_HOME%" --seat-home "%SEATS%" --launcher "%~f0" %2 %3 %4 %5 %6 %7 %8 %9
set "RC=%errorlevel%"
pause
exit /b %RC%
:selftest
rem Read only: runs the kit's self tests and prints what it found. No plugin call, no seat, no shortcut, no session.
"%PY%" -I "%KITDIR%\plugins\ml-seat-kit\scripts\seat_setup.py" selftest --kit-home "%KIT_HOME%"
set "RC=%errorlevel%"
pause
exit /b %RC%
:nopy
echo no Python this kit can use
set "SEEN="
for /f "delims=" %%p in ('where python python3 py 2^>nul') do set "SEEN=1" & echo    found, not used: %%p
if not defined SEEN echo    found: no Python at all
set "N=2" & set "WHY=%NOINST% needs a Python from python.org (a Microsoft Store one is not used) and installs nothing; nothing was changed" & set "TODO=ask IT to install Python 3.12 for your user from python.org, then run this again" & goto stop
:savepy
>"%KIT_HOME%\python.txt" echo(%PY%
exit /b 0
:dropstore
rem Adopt and selftest never use a Microsoft Store Python (under WindowsApps). Only called with PY set, so the edit below is safe.
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
if defined NOINST if defined PY call :dropstore
if not defined PY for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%d\python.exe" set "PY=%%d\python.exe"
if not defined PY for /f "delims=" %%p in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%p"
if defined NOINST if defined PY call :dropstore
if not defined PY for /f "delims=" %%p in ('where python 2^>nul ^| findstr /v /i WindowsApps') do if not defined PY set "PY=%%p"
if defined PY "%PY%" -I -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>nul || set "PY="
exit /b 0
:stop
echo.
echo Stopped at step %N%: %WHY%.
echo What to do: %TODO%
pause
exit /b 1
