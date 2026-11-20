@echo off
rem Starts the WhatsApp bot supervisor from the repo root (used by Task Scheduler at logon).
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python -m wabot >> data\wa\wabot.log 2>&1
