@echo off
REM ============================================================
REM  START_SAD.bat  -  one-click launcher for the SAD viewer.
REM  Opens the match server (8000) and page server (5500) each in
REM  their own window, waits, then opens the map in your browser.
REM  Double-click it, or run:  START_SAD.bat
REM ============================================================
setlocal
set "BAT=C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
set "CODE=C:\Users\jmeyers\Desktop\Detroit_Test\code"
set "DATA=C:\Users\jmeyers\Desktop\Detroit_Test\data"
set "URL=http://localhost:5500/_compare_ui/map.html"

echo Starting SAD match server (port 8000)...
start "SAD match server (8000)" cmd /k ""%BAT%" "%CODE%\sad_match_server.py" --data-dir "%DATA%""

echo Starting SAD page server (port 5500)...
start "SAD page server (5500)" cmd /k "cd /d "%DATA%" && "%BAT%" -m http.server 5500"

echo Waiting for the match server to come up...
REM poll /health up to ~40s (2s x 20); the page server is instant
set /a tries=0
:wait
timeout /t 2 /nobreak >nul
set /a tries+=1
curl -s -o nul "http://localhost:8000/health" && goto ready
if %tries% GEQ 20 goto giveup
goto wait

:ready
echo Match server is up.
goto openbrowser

:giveup
echo Match server not confirmed yet (first boot can be slow: corpus + nature).
echo Opening the page anyway; drawing will work once the 8000 window is ready.

:openbrowser
start "" "%URL%"
echo.
echo Opened %URL%
echo Leave both server windows open. Close them to stop the servers.
endlocal
