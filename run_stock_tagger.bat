@echo off
chcp 65001 >nul
title StockAI Tagger — Launching
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw stock_tagger_app.py
    exit /b 0
)

if exist "StockAI_Tagger.exe" (
    start "" "StockAI_Tagger.exe"
    exit /b 0
)

echo [!] Python or StockAI_Tagger.exe not found!
pause
