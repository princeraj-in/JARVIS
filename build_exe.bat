@echo off
REM ============================================================
REM  J.A.R.V.I.S. — build a standalone Windows .exe
REM  Run this from the same folder as jarvis.py
REM ============================================================

echo Installing/updating dependencies...
pip install -r requirements.txt

echo.
echo Generating app icon (if not already present)...
python -c "import jarvis; jarvis.ensure_app_icon()"

echo.
echo Building JARVIS.exe with PyInstaller...
pyinstaller --noconfirm --onefile --windowed ^
    --name "JARVIS" ^
    --icon "jarvis_icon.ico" ^
    --collect-all PyQt6 ^
    --collect-all edge_tts ^
    --collect-all pywhatkit ^
    --collect-all playwright ^
    jarvis.py

echo.
echo ============================================================
echo  Done. Find JARVIS.exe inside the "dist" folder.
echo  jarvis_config.json, jarvis_memory.db, and jarvis.log will be
echo  created next to the .exe the first time you run it.
echo ============================================================
pause
