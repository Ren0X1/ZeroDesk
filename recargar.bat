@echo off
rem Recarga el monedero. La ventana se cierra sola al terminar: lo que ha
rem pasado te llega por Telegram y queda en logs\recarga-AAAA-MM-DD.log
cd /d "%~dp0"
python -u recarga.py
exit /b %errorlevel%
