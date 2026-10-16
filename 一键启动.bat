@echo off
rem Tianshu UTM - one click dev launcher.
rem Preflight, then Flask on 5000, then Vite on 5173, then the browser.
rem Keep this file in the project root; it finds the code folder by marker files.
setlocal EnableExtensions
title Tianshu Launcher
cd /d "%~dp0" || exit /b 1

set "API_PORT=5000"
set "WEB_PORT=5173"

rem ---- locate the code folder: backend/ once moved into a subfolder and this
rem ---- script kept pointing at the old path, so resolve it instead of hard-coding.
set "BACKEND="
set "FRONTEND="
if exist "%~dp0backend\run.py" (
  set "BACKEND=%~dp0backend"
  set "FRONTEND=%~dp0frontend"
)
for /d %%D in ("%~dp0*") do (
  if exist "%%D\backend\run.py" (
    set "BACKEND=%%D\backend"
    set "FRONTEND=%%D\frontend"
  )
)
if defined BACKEND goto located
echo [x] No backend\run.py under this folder, so there is nothing to launch.
echo     Put the two .bat files back into the project root.
pause
exit /b 1
:located

echo ============================================
echo    Tianshu UTM  -  dev launcher
echo ============================================
echo   backend  : %BACKEND%
echo   frontend : %FRONTEND%
echo.

if exist "%BACKEND%\.venv\Scripts\python.exe" goto venv_ok
echo [x] Backend virtualenv is missing. Create it once:
echo     cd /d "%BACKEND%"
echo     python -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
:venv_ok

if exist "%FRONTEND%\node_modules\vite\bin\vite.js" goto deps_ok
echo [x] Frontend dependencies are missing. Install once:
echo     cd /d "%FRONTEND%"
echo     npm install
pause
exit /b 1
:deps_ok

where npm >nul 2>&1
if errorlevel 1 (
  echo [x] npm is not on PATH - install Node.js, then run this again.
  pause
  exit /b 1
)

if exist "%BACKEND%\.env" goto env_ok
echo [!] backend\.env is missing: copy .env.example and set the MySQL password.
:env_ok

call :port_up 3306
if errorlevel 1 echo [!] Nothing on 3306 - start MySQL or every API call will fail.
echo.

echo [1/2] backend   http://127.0.0.1:%API_PORT%/api/health
call :serve %API_PORT% tianshu-backend "%BACKEND%" ".venv\Scripts\python.exe run.py" 60
if errorlevel 1 exit /b 1

echo [2/2] frontend  http://localhost:%WEB_PORT%
call :serve %WEB_PORT% tianshu-frontend "%FRONTEND%" "npm run dev" 90
if errorlevel 1 exit /b 1

echo.
echo ============================================
echo   Ready.
echo     console : http://localhost:%WEB_PORT%/
echo     screen  : http://localhost:%WEB_PORT%/screen
echo     api     : http://127.0.0.1:%API_PORT%/api/health
echo   demo accounts : admin / merchant / customer / pilot
echo   demo password : Admin@123   (see frontend README.md)
echo   stop with the other .bat in this folder.
echo ============================================
where curl.exe >nul 2>&1
if not errorlevel 1 (
  echo   health:
  curl -s -m 3 "http://127.0.0.1:%API_PORT%/api/health" | findstr /C:"status"
)
start "" "http://localhost:%WEB_PORT%/"
pause
exit /b 0

:serve
rem %1=port %2=window title %3=work dir %4=command %5=max wait seconds
call :port_up %1
if not errorlevel 1 (
  echo       already listening on %1, left running.
  exit /b 0
)
start "%~2" /min cmd /c "cd /d ""%~3"" && %~4"
call :wait_port %1 %~5
if errorlevel 1 (
  echo [x] Nothing opened port %1 within %~5 seconds.
  echo     Un-minimize the "%~2" window and read the error - usual causes are
  echo     MySQL down, a wrong .env, or the port held by another program.
  pause
  exit /b 1
)
echo       up on %1.
exit /b 0

:port_up
rem %1 = TCP port; returns 0 when something is LISTENING on it.
netstat -aon | findstr /C:":%1 " | findstr /C:"LISTENING" >nul
exit /b %errorlevel%

:wait_port
rem %1 = port, %2 = seconds to wait before giving up
set "TS_WAIT=0"
:wait_loop
call :port_up %1
if not errorlevel 1 exit /b 0
call :sleep 1
set /a TS_WAIT+=1
if %TS_WAIT% LSS %2 goto wait_loop
exit /b 1

:sleep
rem %1 = whole seconds. ping waits even with redirected stdin, timeout does not.
set /a TS_PINGS=%1+1
ping -n %TS_PINGS% 127.0.0.1 >nul 2>&1
exit /b 0
