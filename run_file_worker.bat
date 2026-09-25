@echo off
chcp 65001 >nul
title Immich Caption File Worker
cd /d "%~dp0"
echo ===================================================
echo   Immich Caption File Worker (LM Studio Client)
echo ===================================================
python file_worker.py
pause
