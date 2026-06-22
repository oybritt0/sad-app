@echo off
title SAD Viewer
cd /d "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"

REM --- Stage viewer assets into the served _ui folder -------------------------
REM The server serves data\_ui\, not code\viewer\. Copy fresh source files in
REM on every launch so edits in code\viewer\ always show up without a rebuild.
set "UI=C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data\_ui"
if not exist "%UI%" mkdir "%UI%"
copy /Y "viewer\index.html"            "%UI%\index.html"            >nul
copy /Y "viewer\viewer.js"             "%UI%\viewer.js"             >nul
copy /Y "viewer\viewer.css"            "%UI%\viewer.css"            >nul
copy /Y "viewer\viewer_modules.js"     "%UI%\viewer_modules.js"     >nul
copy /Y "viewer\viewer_integration.js" "%UI%\viewer_integration.js" >nul
copy /Y "viewer\logo.png"              "%UI%\logo.png"              >nul
echo Staged viewer assets into %UI%

python ui_server.py --data-dir "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data" --no-open
pause

