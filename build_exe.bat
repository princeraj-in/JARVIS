@echo off
setlocal
REM ============================================================
REM  J.A.R.V.I.S. — Build a standalone Windows .exe
REM  Run this from the same folder as jarvis.py
REM ============================================================

echo [1/3] Installing/updating dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo [2/3] Generating app icon (jarvis_icon.ico)...
python -c "from PIL import Image, ImageDraw; import os; p='jarvis_icon.ico'; size=256; img=Image.new('RGBA',(size,size),(0,0,0,0)); d=ImageDraw.Draw(img); cx=cy=size//2; a=(0,242,254,255); dk=(9,14,23,255); d.ellipse([cx-118,cy-118,cx+118,cy+118],fill=(5,10,18,255)); d.ellipse([cx-110,cy-110,cx+110,cy+110],outline=a,width=6); d.ellipse([cx-82,cy-82,cx+82,cy+82],outline=a,width=4); d.ellipse([cx-48,cy-48,cx+48,cy+48],fill=dk,outline=a,width=5); d.ellipse([cx-10,cy-10,cx+10,cy+10],fill=a); img.save(p,sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]) if not os.path.exists(p) else None"

echo.
echo [3/3] Building JARVIS.exe with PyInstaller...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "JARVIS" ^
    --icon "jarvis_icon.ico" ^
    --collect-all PyQt6 ^
    --collect-all edge_tts ^
    --collect-all pywhatkit ^
    --collect-all playwright ^
    --hidden-import "google.genai" ^
    --hidden-import "googleapiclient" ^
    --hidden-import "google_auth_oauthlib" ^
    --hidden-import "pycaw" ^
    --hidden-import "comtypes" ^
    --hidden-import "screen_brightness_control" ^
    --hidden-import "uiautomation" ^
    jarvis.py

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed! Check the output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done! Find JARVIS.exe inside the "dist" folder.
echo  jarvis_config.json, jarvis_memory.db, and jarvis.log will be
echo  created next to the .exe the first time you run it.
echo ============================================================
pause
