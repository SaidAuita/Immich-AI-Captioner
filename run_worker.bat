@echo off
chcp 65001 >nul
title Immich AI Captioner
cd /d "%~dp0"
echo ===================================================
echo   Immich AI Captioner (LM Studio + RTX 3080 Ti)
echo ===================================================
python main.py
pause
