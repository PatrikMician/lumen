@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Vytvarim virtualni prostredi...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Instaluji a aktualizuji zavislosti...
pip install -q -r requirements.txt
pip install -q -U "yt-dlp[default]"
python run.py
pause
