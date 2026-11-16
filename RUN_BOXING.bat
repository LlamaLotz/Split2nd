@echo off
title Split2nd Boxing
cd /d "%~dp0"
set GATE_CM=60
set PREVIEW_FPS=20
set PREVIEW_WIDTH=640
set CAMERA_MIRROR=0
set BOXING_SCRIPTED=1
echo.
echo  Split2nd Boxing
echo  Open: http://localhost:8443/boxing
echo  Close the Arduino IDE Serial Monitor first.
echo.
.venv\Scripts\python.exe scripts\run.py --no-tls --vision local --rig serial --rig-port COM6
pause
