@echo off
echo Stopping Immich AI Captioner...
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*ImmichCaptioner\main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Stopped.
pause
