@echo off
chcp 65001 >nul
title Immich Caption Coordinator
cd /d "%~dp0"
echo ===================================================
echo   Immich Caption Coordinator (Queue Manager)
echo ===================================================
python coordinator.py
pause
