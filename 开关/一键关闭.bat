@echo off
rem Tianshu UTM - stop the dev services the launcher starts.
rem It only touches ports 5000/5173 and the launcher's own windows, and it never
rem kills python.exe / node.exe by image name, so other Node servers, your editor
rem and a running pytest session are not collateral damage.
setlocal EnableExtensions
title Tianshu Stop
set "API_PORT=5000"
set "WEB_PORT=5173"

echo Stopping Tianshu dev services ...
echo.

rem 1) the launcher's console windows, child trees included
for %%T in (tianshu-backend tianshu-frontend) do (
  taskkill /F /T /FI "WINDOWTITLE eq %%T*" >nul 2>&1
)

rem 2) anything still on those ports, e.g. a server started from a terminal
for %%P in (%API_PORT% %WEB_PORT%) do (
  call :kill_port %%P
)

rem 3) the reloader and npm need a moment to unwind, then one second attempt
call :sleep 2
for %%P in (%API_PORT% %WEB_PORT%) do (
  call :port_up %%P
  if not errorlevel 1 call :kill_port %%P
)

call :sleep 1
echo.
for %%P in (%API_PORT% %WEB_PORT%) do (
  call :report %%P
)
echo.
pause
exit /b 0

:kill_port
rem %1 = TCP port; kill each LISTENING owner of that port with its child tree.
set "TS_HIT="
for /f "tokens=5" %%I in ('netstat -aon ^| findstr /C:":%1 " ^| findstr /C:"LISTENING"') do (
  if not defined TS_PID_%%I (
    set "TS_PID_%%I=1"
    set "TS_HIT=1"
    call :kill_one %%I %1
  )
)
if not defined TS_HIT echo   port %1 : nothing was listening.
exit /b 0

:kill_one
rem %1 = pid, %2 = port. Refuse a PID that is not a dev-server runtime.
tasklist /FI "PID eq %1" /NH /FO CSV 2>nul | findstr /I "python.exe node.exe" >nul
if errorlevel 1 (
  echo   port %2 : pid %1 is not python or node, left alone.
  exit /b 0
)
taskkill /F /T /PID %1 >nul 2>&1
echo   port %2 : stopped pid %1 and its children.
exit /b 0

:report
rem %1 = port
call :port_up %1
if errorlevel 1 (
  echo   port %1 : released.
) else (
  echo   port %1 : still busy, run this script again. Owner:
  netstat -aon | findstr /C:":%1 " | findstr /C:"LISTENING"
)
exit /b 0

:port_up
rem %1 = TCP port; returns 0 when something is LISTENING on it.
netstat -aon | findstr /C:":%1 " | findstr /C:"LISTENING" >nul
exit /b %errorlevel%

:sleep
rem %1 = whole seconds. ping waits even with redirected stdin, timeout does not.
set /a TS_PINGS=%1+1
ping -n %TS_PINGS% 127.0.0.1 >nul 2>&1
exit /b 0
