"""
J.A.R.V.I.S. — PyQt6 Edition (Final Build)
========================================
Frameless, glass-panel dashboard built on PyQt6. All 10 pages are live and
functional: Home, Chat, Voice, Tasks, Automation, Apps & Control,
Files & Memory, Research, Tools, and Settings — plus the first-run Welcome
screen. Resizable window (drag the bottom-right corner grip), and a
PyInstaller build script is included to package this as a standalone .exe.

INSTALL (Windows, Python 3.12+):
    pip install -r requirements.txt

RUN:
    python jarvis_pyqt.py

BUILD A STANDALONE .EXE:
    build_exe.bat          (see that file — runs PyInstaller for you)
    Output lands in the "dist" folder as JARVIS.exe. jarvis_config.json and
    jarvis_memory.db are created next to the .exe on first run, same as
    when running from source.

HONEST NOTES ON THE "GLASS/BLUR" LOOK:
  - Card panels use real translucency (rgba backgrounds) + soft drop
    shadows — this is standard, robust glassmorphism and looks like the
    reference image's cards.
  - The reactor logo has a genuine QGraphicsBlurEffect glow behind it —
    real Qt blur, not a fake.
  - What this does NOT do: blur whatever's behind the window on your
    actual Windows desktop (true "Acrylic"). That needs undocumented,
    Windows-version-specific OS APIs and is fragile across Windows
    10/11 updates — left out on purpose to keep this stable.
  - The window is frameless with a custom title bar (drag to move,
    minimize, close) and IS resizable via a QSizeGrip in the
    bottom-right corner — Qt's own built-in, safe resize handle
    (no custom edge-drag hit-testing, which is a common source of
    frameless-window bugs).

CONFIG: per-user name + API keys are stored in jarvis_config.json next to
this script. GEMINI_API_KEYS environment variable (comma-separated) is
used as a fallback on first run. Up to 8 keys, instant automatic failover.
"""

import os
import tempfile
import sys
import re
import json
import time
import random
import sqlite3
import threading
import asyncio
import subprocess
import webbrowser
import ctypes
import logging
from logging.handlers import RotatingFileHandler
try:
    import winsound
except ImportError:
    winsound = None

IS_WINDOWS = sys.platform.startswith("win")

def beep(freq: int, dur_ms: int):
    """Cross-platform-safe beep. No-op if unsupported (non-Windows, or winsound missing)."""
    if winsound:
        try:
            winsound.Beep(freq, dur_ms)
        except Exception:
            pass
from datetime import datetime, timedelta
from PIL import Image, ImageGrab, ImageDraw

# ==========================================
# 0. LOGGING (essential once packaged as a --windowed .exe — that build has
#    NO console, so print() output is otherwise completely invisible)
# ==========================================
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.log")
logger = logging.getLogger("jarvis")
logger.setLevel(logging.INFO)
try:
    _file_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_file_handler)
except Exception as _e:
    print(f"[Logging setup failed: {_e}]")

def log_info(msg: str):
    logger.info(msg)
    print(f"[INFO] {msg}")

def log_error(msg: str):
    logger.error(msg)
    print(f"[ERROR] {msg}")

from PyQt6.QtCore import (Qt, QObject, pyqtSignal, QTimer, QPoint, QPointF, QRectF, QSize,
                          QPropertyAnimation, QEasingCurve)
from PyQt6.QtGui import (QFont, QColor, QPainter, QPen, QCursor, QTextCursor, QPixmap,
                          QRadialGradient, QBrush, QIcon, QPolygonF)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QLineEdit, QTextEdit,
    QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QScrollArea,
    QStackedWidget, QGraphicsDropShadowEffect, QGraphicsBlurEffect, QFileDialog,
    QMenu, QProgressBar, QSizePolicy, QInputDialog, QSizeGrip, QGraphicsOpacityEffect,
    QSystemTrayIcon, QMessageBox
)

import requests
import pygame
import edge_tts
import keyboard
from google import genai
from google.genai import types

try:
    import speech_recognition as sr
except ImportError:
    sr = None
try:
    import screen_brightness_control as sbc
except ImportError:
    sbc = None
try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL
    from ctypes import cast, POINTER
except ImportError:
    AudioUtilities = None
try:
    import psutil
except ImportError:
    psutil = None

try:
    pygame.mixer.init()
except Exception:
    pass

# ==========================================
# 1. LOCAL CONFIG (per-user name + API keys)
# ==========================================
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_config.json")
MAX_KEYS = 8

def load_config() -> dict:
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[Config Load Error: {e}]")
    return {}

def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[Config Save Error: {e}]")

_cfg = load_config()
IS_FIRST_RUN = not bool(_cfg.get("user_name"))
USER_NAME = _cfg.get("user_name", "User")
GREETING_ENABLED = bool(_cfg.get("greeting_enabled", True))
GREETING_LANGUAGE = _cfg.get("greeting_language", "english")  # "english" or "hindi"
SYSTEM_AUTOMATION_ENABLED = bool(_cfg.get("system_automation_enabled", False))  # shell/UI-control/self-writing tools — OFF by default

def is_system_automation_enabled() -> bool:
    return SYSTEM_AUTOMATION_ENABLED

if _cfg.get("gemini_keys"):
    GEMINI_KEYS = list(_cfg["gemini_keys"])
else:
    _env_keys = os.environ.get("GEMINI_API_KEYS", "")
    GEMINI_KEYS = [k.strip() for k in _env_keys.split(",") if k.strip()]

DB_PATH = "jarvis_memory.db"
gemini_key_index = 0

VOICE_PROFILES = {
    # persona: (fallback voices in order, rate, pitch)
    "JARVIS": (["en-IN-PrabhatNeural", "en-GB-RyanNeural", "en-US-AndrewMultilingualNeural"], "-4%", "-3Hz"),
    "FRIDAY": (["en-IN-NeerjaExpressiveNeural", "en-IN-NeerjaNeural", "en-US-AvaMultilingualNeural"], "+0%", "+1Hz"),
    "ELEVEN": (["en-IN-AartiNeural", "en-IN-NeerjaNeural", "en-US-EmmaMultilingualNeural"], "-2%", "+2Hz"),
}
HINDI_VOICES = {"JARVIS": ["hi-IN-MadhurNeural"], "FRIDAY": ["hi-IN-SwaraNeural"], "ELEVEN": ["hi-IN-SwaraNeural"]}

def tts_voice_name() -> str:
    p = PERSONAS.get(CURRENT_PERSONA, PERSONAS["JARVIS"]) if "PERSONAS" in globals() else None
    if GREETING_LANGUAGE == "hindi":
        return "hi-IN-MadhurNeural" if CURRENT_PERSONA == "JARVIS" else "hi-IN-SwaraNeural"
    if p:
        return p["voice_en"]
    return "en-US-ChristopherNeural"

THEMES = {
    "Arc Cyan": {"accent": "#00f2fe", "primary": "#0284c7", "hover": "#22d3ee"},
    "Matrix Emerald": {"accent": "#10b981", "primary": "#059669", "hover": "#34d399"},
    "Stark Crimson": {"accent": "#f59e0b", "primary": "#dc2626", "hover": "#ef4444"},
    "Stark Ruby": {"accent": "#ff2e63", "primary": "#dc2626", "hover": "#f43f5e"},
    "Cyber Amethyst": {"accent": "#a855f7", "primary": "#7c3aed", "hover": "#c084fc"},
}
current_theme_key = _cfg.get("theme", "Arc Cyan")
if current_theme_key not in THEMES:
    current_theme_key = "Arc Cyan"

# ==========================================
# 1c. PERSONAS — same backend/logic for all three; only name, colors,
#     reactor shape, and voice differ. Each persona is fully capable on
#     its own (no feature lock); delegation between personas is a
#     SEPARATE capability layered on top (section 4d), not a limitation.
# ==========================================
PERSONAS = {
    "JARVIS": {
        "full_name": "J.A.R.V.I.S.", "icon": "◎",
        "tagline": "JUST A RATHER VERY INTELLIGENT SYSTEM",
        "theme_key": "Arc Cyan", "reactor_shape": "rings",
        "voice_en": "en-IN-PrabhatNeural",
        "greeting_en": "Hello {name}, how can I help you today?",
        "greeting_hi": "Namaste {name}, main aapki kaise madad kar sakta hoon?",
        "wake_word": "jarvis",
    },
    "FRIDAY": {
        "full_name": "F.R.I.D.A.Y.", "icon": "✦",
        "tagline": "YOUR PERSONAL AI ASSISTANT",
        "theme_key": "Stark Ruby", "reactor_shape": "hexagon",
        "voice_en": "en-IN-NeerjaExpressiveNeural",
        "greeting_en": "Friday online. All systems ready, {name}.",
        "greeting_hi": "Friday taiyar hai, {name}. Sab kuch ready hai.",
        "wake_word": "friday",
    },
    "ELEVEN": {
        "full_name": "E.L.E.V.E.N.", "icon": "⬡",
        "tagline": "EVOLVING LOGIC ENGINE",
        "theme_key": "Cyber Amethyst", "reactor_shape": "orbital",
        "voice_en": "en-IN-AartiNeural",
        "greeting_en": "Eleven online, {name}. Systems ready.",
        "greeting_hi": "Eleven taiyar hai, {name}. Systems ready hain.",
        "wake_word": "eleven",
    },
}
CURRENT_PERSONA = _cfg.get("persona", "JARVIS")
if CURRENT_PERSONA not in PERSONAS:
    CURRENT_PERSONA = "JARVIS"
current_theme_key = PERSONAS[CURRENT_PERSONA]["theme_key"]  # persona owns the color, overrides any stale saved theme

def persona() -> dict:
    return PERSONAS.get(CURRENT_PERSONA, PERSONAS["JARVIS"])

def switch_persona(name: str):
    """The single source of truth for changing the active persona — updates
    the in-memory state and persists it. UI refresh is handled separately
    by MainWindow.apply_persona_everywhere() so this stays UI-agnostic."""
    global CURRENT_PERSONA, current_theme_key
    name = name.upper()
    if name not in PERSONAS or name == CURRENT_PERSONA:
        return False
    CURRENT_PERSONA = name
    current_theme_key = PERSONAS[name]["theme_key"]
    cfg = load_config()
    cfg["persona"] = CURRENT_PERSONA
    cfg["theme"] = current_theme_key
    save_config(cfg)
    return True

JARVIS_QUOTES = [
    "Sometimes you gotta run before you can walk.",
    "I am always designing upgrades, if that's what you're asking.",
    "There's no need to be afraid of a little criticism.",
    "A little risky, but I think it's worth it, sir.",
]

# ==========================================
# 1b. APP ICON (generated in code — no external image file needed)
# ==========================================
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_icon.ico")

def ensure_app_icon() -> str:
    """Draws a simple reactor-style icon and saves it as a multi-size .ico
    next to this script, if one doesn't already exist. Used for the window/
    taskbar icon and the system tray icon, and can be pointed to by
    build_exe.bat's --icon flag for the packaged .exe too."""
    if os.path.exists(ICON_PATH):
        return ICON_PATH
    try:
        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        cx = cy = size // 2
        accent_rgb = (0, 242, 254, 255)
        dark_rgb = (9, 14, 23, 255)
        d.ellipse([cx - 118, cy - 118, cx + 118, cy + 118], fill=(5, 10, 18, 255))
        d.ellipse([cx - 110, cy - 110, cx + 110, cy + 110], outline=accent_rgb, width=6)
        d.ellipse([cx - 82, cy - 82, cx + 82, cy + 82], outline=accent_rgb, width=4)
        d.ellipse([cx - 48, cy - 48, cx + 48, cy + 48], fill=dark_rgb, outline=accent_rgb, width=5)
        d.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=accent_rgb)
        img.save(ICON_PATH, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception as e:
        print(f"[Icon generation failed: {e}]")
        return ""
    return ICON_PATH

# ==========================================
# 2. SQLITE DATABASE
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, title TEXT, created_at REAL, is_pinned INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, attachment TEXT, timestamp REAL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, seconds INTEGER,
            created_at REAL, fire_at REAL, completed INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS memory_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT, created_at REAL)""")
    try:
        cur.execute("ALTER TABLE sessions ADD COLUMN is_pinned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def db_save_session(session_id, title, created_at, is_pinned=0):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?)", (session_id, title, created_at, is_pinned))
    conn.commit()
    conn.close()

def db_update_session_title(session_id, title):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
    conn.commit()
    conn.close()

def db_toggle_pin(session_id, is_pinned):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE sessions SET is_pinned = ? WHERE id = ?", (is_pinned, session_id))
    conn.commit()
    conn.close()

def db_delete_session(session_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    cur.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()

def db_save_message(session_id, role, content, attachment=""):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO messages (session_id, role, content, attachment, timestamp) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, attachment or "", time.time()))
    conn.commit()
    conn.close()

def db_load_sessions():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, created_at, is_pinned FROM sessions ORDER BY is_pinned DESC, created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def db_load_messages(session_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT role, content, attachment FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def db_recent_activity(limit=6):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT role, content, timestamp FROM messages WHERE role='user' ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def db_add_reminder(label, seconds) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = time.time()
    cur.execute("INSERT INTO reminders (label, seconds, created_at, fire_at, completed) VALUES (?, ?, ?, ?, 0)",
                (label, seconds, now, now + seconds))
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid

def db_mark_reminder_done(rid):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE reminders SET completed = 1 WHERE id = ?", (rid,))
    conn.commit()
    conn.close()

def db_delete_reminder(rid):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM reminders WHERE id = ?", (rid,))
    conn.commit()
    conn.close()

def db_load_reminders():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, label, seconds, created_at, fire_at, completed FROM reminders ORDER BY fire_at ASC")
    rows = cur.fetchall()
    conn.close()
    return rows

def db_add_memory_fact(fact: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO memory_facts (fact, created_at) VALUES (?, ?)", (fact.strip(), time.time()))
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return fid

def db_load_memory_facts():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, fact, created_at FROM memory_facts ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def db_delete_memory_fact(fid: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM memory_facts WHERE id = ?", (fid,))
    conn.commit()
    conn.close()

def build_memory_context() -> str:
    """Formats stored long-term facts for injection into the AI's system
    prompt, so it can use them naturally without the user repeating them."""
    try:
        facts = db_load_memory_facts()
    except Exception:
        facts = []
    if not facts:
        return ""
    lines = [f"- {f[1]}" for f in facts[:30]]
    return ("\n\nKnown facts about the user (use naturally where relevant, "
            "don't just list them back):\n" + "\n".join(lines))

def remember_fact(fact: str) -> str:
    """Saves a fact about the user permanently, so future conversations
    (and the voice assistant) can use it without being told again."""
    if not fact or not fact.strip():
        return "Nothing to remember — the fact was empty."
    db_add_memory_fact(fact.strip())
    return f"Got it, I'll remember: {fact.strip()}"

init_db()

# ==========================================
# 3. TEXT / CODE PARSER
# ==========================================
def clean_display_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\*{1,3}", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\*\-\+]\s+", "• ", text, flags=re.MULTILINE)
    return text.strip()

def parse_content_blocks(raw_text: str):
    blocks = []
    pattern = r"```([a-zA-Z0-9_\-\+]*)\n(.*?)```"
    last_end = 0
    for match in re.finditer(pattern, raw_text, re.DOTALL):
        start, end = match.span()
        if start > last_end:
            text_part = clean_display_text(raw_text[last_end:start])
            if text_part:
                blocks.append({"type": "text", "content": text_part})
        lang = match.group(1).strip() or "PYTHON"
        code_content = match.group(2).strip()
        blocks.append({"type": "code", "lang": lang.upper(), "content": code_content})
        last_end = end
    if last_end < len(raw_text):
        remaining_text = clean_display_text(raw_text[last_end:])
        if remaining_text:
            blocks.append({"type": "text", "content": remaining_text})
    if not blocks:
        blocks.append({"type": "text", "content": clean_display_text(raw_text)})
    return blocks

# ==========================================
# 4. HARDWARE & AUTOMATION TOOLS (unchanged logic)
# ==========================================
def set_system_volume(level_or_action: str = "50") -> str:
    """Controls Windows Master Audio Volume (0-100%, mute, unmute)."""
    clean = level_or_action.lower().strip()
    try:
        if AudioUtilities:
            devices = AudioUtilities.GetSpeakers()
            if hasattr(devices, "EndpointVolume"):      # newer pycaw returns an AudioDevice wrapper
                volume = devices.EndpointVolume
            else:
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))
            if "mute" in clean and "unmute" not in clean:
                volume.SetMute(1, None)
                return "Master audio muted."
            elif "unmute" in clean:
                volume.SetMute(0, None)
                return "Master audio unmuted."
            val_match = re.search(r'(\d+)', clean)
            if val_match:
                pct = max(0, min(100, int(val_match.group(1))))
                volume.SetMasterVolumeLevelScalar(pct / 100.0, None)
                return f"System volume set to {pct}%."
        return "Volume control executed."
    except Exception as e:
        return f"Volume Control Error: {e}"

def set_screen_brightness(level: str = "70") -> str:
    """Controls Windows display brightness (0-100%)."""
    try:
        if sbc:
            val_match = re.search(r'(\d+)', str(level))
            if val_match:
                pct = max(10, min(100, int(val_match.group(1))))
                sbc.set_brightness(pct)
                return f"Screen brightness set to {pct}%."
        return "Brightness adjusted."
    except Exception as e:
        return f"Brightness Error: {e}"

def create_reminder(label: str, seconds: int) -> str:
    """Creates a background timer/reminder, persists it in SQLite (so the Tasks
    page can list it), and beeps + marks it done on completion."""
    seconds = max(1, int(seconds))
    rid = db_add_reminder(label, seconds)

    def _timer_thread():
        time.sleep(seconds)
        if winsound:
            try:
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
            except Exception:
                pass
        db_mark_reminder_done(rid)
        log_info(f"Reminder fired: {label}")
        bridge.reminder_fired.emit(label)

    threading.Thread(target=_timer_thread, daemon=True).start()
    if seconds < 60:
        unit = f"{seconds} second(s)"
    elif seconds < 3600:
        unit = f"{seconds // 60} minute(s)"
    else:
        unit = f"{seconds // 3600} hour(s)"
    return f"Reminder '{label}' set for {unit}. J.A.R.V.I.S. will alert you when it's done."

def set_reminder_timer(timer_query: str) -> str:
    """Parses a natural-language timer request, persists it, and creates it."""
    try:
        num_match = re.search(r'(\d+)\s*(sec|min|second|minute|hour)', timer_query.lower())
        seconds = 30
        if num_match:
            val = int(num_match.group(1))
            u = num_match.group(2)
            if "min" in u:
                seconds = val * 60
            elif "hour" in u:
                seconds = val * 3600
            else:
                seconds = val
        label = timer_query.strip()[:60] or "Reminder"
        return create_reminder(label, seconds)
    except Exception as e:
        return f"Timer Error: {e}"

def play_youtube_video(query_or_song: str = "top hits") -> str:
    """Plays music or videos on YouTube. If a Google account is connected
    (YouTube Data API v3 enabled on the same project), searches via the
    real API and opens the exact top result; otherwise falls back to the
    old pywhatkit/browser-search behaviour."""
    clean_q = re.sub(r"^(play|open\s+channel|search)\s+", "", query_or_song.strip(), flags=re.IGNORECASE)
    if GOOGLE_LIBS_AVAILABLE and google_is_connected():
        try:
            creds = google_get_credentials()
            service = _google_build("youtube", "v3", credentials=creds)
            res = service.search().list(q=clean_q, part="snippet", type="video", maxResults=1).execute()
            items = res.get("items", [])
            if items:
                video_id = items[0]["id"]["videoId"]
                title = items[0]["snippet"]["title"]
                webbrowser.open(f"https://www.youtube.com/watch?v={video_id}")
                return f"Playing '{title}' on YouTube."
        except Exception:
            pass  # fall through to the fallback below
    try:
        import pywhatkit
        pywhatkit.playonyt(clean_q)
        return f"Playing '{clean_q}' on YouTube."
    except Exception:
        encoded = requests.utils.quote(query_or_song.strip())
        webbrowser.open(f"https://www.youtube.com/results?search_query={encoded}")
        return f"Opened YouTube search for '{query_or_song}'."

def open_whatsapp(action_detail: str = "") -> str:
    """Performs smart in-app WhatsApp automation."""
    p = action_detail.strip()
    p_low = p.lower()
    try:
        import pyautogui
    except Exception:
        pyautogui = None

    phone_match = re.search(r'(\+?\d{10,13})', p)
    if phone_match:
        phone = phone_match.group(1)
        msg_match = re.search(r'(?:send|msg|text|message|bhejo)\s+(.*?)(?:\s+to\s+|\s+par\s+|$)', p, re.IGNORECASE)
        msg = msg_match.group(1).strip() if msg_match else "Hello"
        encoded = requests.utils.quote(msg)
        webbrowser.open(f"https://web.whatsapp.com/send?phone={phone}&text={encoded}")
        return f"Opening WhatsApp chat with +{phone} and pre-filling message: '{msg}'."

    if "call" in p_low:
        is_video = "video" in p_low
        call_type = "Video Call" if is_video else "Voice Call"
        contact_match = re.search(r'(?:call|karo)\s+(?:to\s+|ko\s+)?([a-zA-Z0-9_\s]+)', p, re.IGNORECASE)
        contact = contact_match.group(1).strip() if contact_match else ""
        if IS_WINDOWS:
            subprocess.Popen("start whatsapp:", shell=True)
        if contact and pyautogui and IS_WINDOWS:
            def _auto_search_call():
                time.sleep(2.5)
                pyautogui.hotkey('ctrl', 'f')
                time.sleep(0.4)
                pyautogui.write(contact, interval=0.04)
                time.sleep(1.0)
                pyautogui.press('enter')
            threading.Thread(target=_auto_search_call, daemon=True).start()
            return f"Opened WhatsApp Desktop and selected contact '{contact}'. Click {call_type} to connect."
        return f"Opened WhatsApp Desktop. Please select contact to start {call_type}."

    if any(k in p_low for k in ["send", "msg", "message", "text", "bhejo", "hi", "hello", "hiii"]):
        contact = ""
        contact_match = re.search(r'(?:to|contact|ko|par)\s+([a-zA-Z0-9_]+)', p, re.IGNORECASE)
        if contact_match:
            contact = contact_match.group(1).strip()
        msg = "Hi"
        msg_match = re.search(r'(?:send|msg|text|message|bhejo)\s+(.*?)(?:\s+to\s+|\s+ko\s+|$)', p, re.IGNORECASE)
        if msg_match:
            msg = msg_match.group(1).strip()
        elif "hiii" in p_low or "hi" in p_low:
            msg = "Hiii"
        if IS_WINDOWS:
            subprocess.Popen("start whatsapp:", shell=True)
        if contact and pyautogui and IS_WINDOWS:
            def _auto_message():
                time.sleep(2.5)
                pyautogui.hotkey('ctrl', 'f')
                time.sleep(0.4)
                pyautogui.write(contact, interval=0.04)
                time.sleep(1.0)
                pyautogui.press('enter')
                time.sleep(0.8)
                pyautogui.write(msg, interval=0.03)
                time.sleep(0.4)
                pyautogui.press('enter')
            threading.Thread(target=_auto_message, daemon=True).start()
            return f"Automating WhatsApp Desktop: searching '{contact}' and sending: '{msg}'."
        else:
            encoded_msg = requests.utils.quote(msg)
            webbrowser.open(f"https://wa.me/?text={encoded_msg}")
            return f"Opened WhatsApp with message: '{msg}'. Select contact to send."

    if IS_WINDOWS:
        subprocess.Popen("start whatsapp:", shell=True)
        return "Opened WhatsApp Desktop."
    webbrowser.open("https://web.whatsapp.com")
    return "Opened WhatsApp Web in your browser."

def search_github(username_or_topic: str = "python") -> str:
    """Searches GitHub users or repositories via public REST API."""
    try:
        clean = username_or_topic.strip().replace("@", "")
        headers = {"User-Agent": "JARVIS_Assistant"}
        url = f"https://api.github.com/users/{requests.utils.quote(clean)}"
        res = requests.get(url, headers=headers, timeout=5).json()
        if "login" in res:
            return (f"GitHub Profile ({res.get('login')}):\n"
                    f"• Name: {res.get('name', 'N/A')}\n"
                    f"• Public Repos: {res.get('public_repos', 0)}\n"
                    f"• Followers: {res.get('followers', 0)}\n"
                    f"• Profile URL: {res.get('html_url')}")
        s_url = f"https://api.github.com/search/repositories?q={requests.utils.quote(clean)}&per_page=3"
        s_res = requests.get(s_url, headers=headers, timeout=5).json()
        items = s_res.get("items", [])
        if items:
            out = ["GitHub Top Repositories:"]
            for it in items:
                out.append(f"• {it.get('full_name')} (★ {it.get('stargazers_count')}): {it.get('html_url')}")
            return "\n".join(out)
        return f"No GitHub profile or repository found for '{username_or_topic}'."
    except Exception as e:
        return f"GitHub Error: {e}"

def open_or_send_gmail(to_email: str = "") -> str:
    """Opens Gmail composer in browser."""
    if to_email:
        enc_to = requests.utils.quote(to_email)
        webbrowser.open(f"https://mail.google.com/mail/?view=cm&fs=1&to={enc_to}")
        return f"Opened Gmail composer for '{to_email}'."
    webbrowser.open("https://mail.google.com")
    return "Opened Gmail inbox."

def generate_qr_code(data_or_url: str = "https://google.com") -> str:
    """Generates a QR code image and saves it locally. Returns a QR_FILE: marker so
    the chat UI renders it inline instead of shelling out to an external viewer."""
    try:
        clean_data = data_or_url.strip()
        encoded = requests.utils.quote(clean_data)
        qr_api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=350x350&data={encoded}"
        img_res = requests.get(qr_api_url, timeout=8)
        if img_res.status_code == 200:
            qr_filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_qr_code.png")
            with open(qr_filename, "wb") as f:
                f.write(img_res.content)
            return f"QR_FILE:{qr_filename}"
        return "Failed to generate QR code."
    except Exception as e:
        return f"QR Error: {e}"

def scrape_webpage(url: str) -> str:
    """Scrapes main text from public web URL."""
    try:
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=6)
        res.raise_for_status()
        html = res.text
        cleaned = re.sub(r'<(script|style).*?>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', cleaned)
        text = re.sub(r'\s+', ' ', text).strip()
        return f"Scraped Content [{url}]:\n{text[:1800]}"
    except Exception as e:
        return f"Scraper Error: {e}"

def search_wikipedia(query: str) -> str:
    """Searches Wikipedia encyclopedia with instant knowledge fallback."""
    try:
        headers = {"User-Agent": "JARVIS_AI_Assistant/1.0"}
        search_url = "https://en.wikipedia.org/w/api.php"
        params = {"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 2}
        res = requests.get(search_url, params=params, headers=headers, timeout=4).json()
        search_results = res.get("query", {}).get("search", [])
        summaries = []
        for item in search_results:
            t = item.get("title")
            s_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(t)}"
            sum_res = requests.get(s_url, headers=headers, timeout=4).json()
            if "extract" in sum_res:
                summaries.append(f"• {t}: {sum_res['extract']}")
        if summaries:
            return "\n\n".join(summaries)
    except Exception:
        pass
    return f"Synthesizing structured knowledge base response for '{query}'."

def take_screenshot(action: str = "capture") -> str:
    """Captures desktop screenshot and saves it locally. Returns an SS_FILE: marker
    so the chat UI renders it inline instead of shelling out to an external viewer."""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        screen_file = os.path.join(base_dir, f"screenshot_{int(time.time())}.png")
        img = ImageGrab.grab()
        img.save(screen_file)
        return f"SS_FILE:{screen_file}"
    except Exception as e:
        return f"Screenshot Error: {e}"

def close_application(app_name: str) -> str:
    """Terminates running applications on Windows by app name."""
    clean = app_name.lower().strip()
    proc_map = {
        "chrome": "chrome.exe", "google chrome": "chrome.exe", "notepad": "notepad.exe",
        "calculator": "CalculatorApp.exe", "calc": "CalculatorApp.exe", "vscode": "Code.exe",
        "vs code": "Code.exe", "spotify": "Spotify.exe", "whatsapp": "WhatsApp.exe",
        "telegram": "Telegram.exe", "discord": "Discord.exe", "cmd": "cmd.exe", "terminal": "cmd.exe",
    }
    if not IS_WINDOWS:
        return "Closing applications this way is only supported on Windows."
    target = proc_map.get(clean, f"{clean}.exe")
    try:
        res = subprocess.run(f"taskkill /f /im {target}", shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            return f"Successfully closed {target}."
        return f"No active process found for '{app_name}' ({target})."
    except Exception as e:
        return f"Close Error: {e}"

def get_system_status(query: str = "status") -> str:
    """Hardware telemetry monitor."""
    try:
        if not psutil:
            return "Hardware Error: psutil not installed."
        cpu = psutil.cpu_percent(interval=0.3)
        ram = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        bat_str = f"{battery.percent}% ({'Plugged In' if battery.power_plugged else 'On Battery'})" if battery else "N/A"
        return (f"System Telemetry: CPU: {cpu}% | RAM: {ram.percent}% "
                f"({round(ram.used/(1024**3), 2)} GB / {round(ram.total/(1024**3), 2)} GB) | Battery: {bat_str}")
    except Exception as e:
        return f"Hardware Error: {e}"

def get_crypto_price(symbol: str = "BTCUSDT") -> str:
    """Fetches live crypto price from Binance."""
    try:
        sym = symbol.upper().replace("/", "").replace("-", "")
        if not any(sym.endswith(x) for x in ["USDT", "BTC"]):
            sym += "USDT"
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}"
        res = requests.get(url, timeout=5).json()
        if "lastPrice" in res:
            return f"Binance Ticker ({sym}): Price: ${float(res['lastPrice']):,.2f} ({float(res['priceChangePercent']):+.2f}%)"
        return f"Crypto pair '{symbol}' not found."
    except Exception as e:
        return f"Crypto Error: {e}"

def convert_forex_currency(amount: str = "1.0", from_curr: str = "USD", to_curr: str = "INR") -> str:
    """Converts forex currency pairs."""
    try:
        amt = float(amount)
        f_c, t_c = from_curr.upper().strip(), to_curr.upper().strip()
        url = f"https://api.frankfurter.app/latest?amount={amt}&from={f_c}&to={t_c}"
        res = requests.get(url, timeout=5).json()
        rates = res.get("rates", {})
        if t_c in rates:
            return f"Forex Rate ({f_c} -> {t_c}): {amt:,.2f} {f_c} = {rates[t_c]:,.2f} {t_c}"
        return "Conversion unavailable."
    except Exception as e:
        return f"Forex Error: {e}"

def calculate_lot_size(balance: str = "10000", risk_pct: str = "1.0", stop_loss_pips: str = "30") -> str:
    """Calculates forex standard lot size."""
    try:
        b, r, sl = float(balance), float(risk_pct), float(stop_loss_pips)
        risk_amt = b * (r / 100.0)
        lot_size = risk_amt / (sl * 10.0)
        return f"Risk: ${risk_amt:,.2f} ({r}%) | Stop Loss: {sl} pips | Recommended: {lot_size:.2f} Standard Lots"
    except Exception as e:
        return f"Lot Calc Error: {e}"

def extract_file_content(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            extracted = [p.extract_text() for p in reader.pages[:20] if p.extract_text()]
            return "\n".join(extracted) if extracted else "No readable text found in PDF."
        except Exception as e:
            return f"PDF Error: {e}"
    return ""

# ==========================================
# 4b. GOOGLE WORKSPACE (Gmail / Drive / Docs)
# ==========================================
# credentials.json is downloaded once from Google Cloud Console (OAuth
# client, "Desktop app" type) and placed next to this script — it is
# NEVER embedded in code. token.json is generated locally the first time
# you connect and is reused/refreshed after that. Both stay local only.
try:
    from google.auth.transport.requests import Request as _GoogleAuthRequest
    from google.oauth2.credentials import Credentials as _GoogleCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow as _GoogleInstalledAppFlow
    from googleapiclient.discovery import build as _google_build
    from googleapiclient.http import MediaFileUpload as _GoogleMediaFileUpload
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

# ==========================================
# 4c. NEXT-GEN AGENT TOOLS — Deep Shell, Excel, UI Automation, Self-Writing
#     Tools, Multi-Agent Swarm. All gated behind SYSTEM_AUTOMATION_ENABLED
#     (Settings/Welcome permission toggle, OFF by default) and, for
#     anything destructive/system-level, an explicit confirm dialog too.
# ==========================================
try:
    import uiautomation as _auto
    UIAUTOMATION_AVAILABLE = True
except ImportError:
    UIAUTOMATION_AVAILABLE = False

try:
    import openpyxl
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

# ---- Deep Shell & System Engineer Mode ----
SHELL_DANGER_KEYWORDS = ["format ", "del /", "rm -rf", "shutdown", "Remove-Item", "-Force",
                          "diskpart", "vssadmin", "reg delete", "net user", "taskkill /f /im explorer"]

def run_shell_command(command: str) -> str:
    """Runs a PowerShell command and returns its output. ALWAYS asks the
    user to confirm first — this is powerful and can be destructive if the
    AI misjudges a request, so it is never run silently."""
    if not is_system_automation_enabled():
        return "⚠️ System-level automation is disabled. Turn it on in Settings → Permissions to use shell commands."
    is_dangerous = any(k.lower() in command.lower() for k in SHELL_DANGER_KEYWORDS)
    prompt = (f"J.A.R.V.I.S. wants to run this PowerShell command:\n\n{command}\n\n"
              f"{'⚠️ This looks potentially destructive. ' if is_dangerous else ''}Allow it?")
    if not request_confirmation("Confirm Shell Command", prompt):
        return "Command cancelled — you didn't approve it."
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                                 capture_output=True, text=True, timeout=30)
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = out
        if err:
            combined += f"\nSTDERR:\n{err}"
        return combined[:2000] if combined else "Command executed successfully (no output)."
    except subprocess.TimeoutExpired:
        return "Command timed out (30s limit)."
    except Exception as e:
        return f"Shell Error: {e}"

# ---- Excel / Data Artifacts ----
def create_excel_file(filename: str, csv_data: str) -> str:
    """Creates an Excel (.xlsx) file from CSV-formatted text (first line =
    headers, following lines = rows, comma-separated) and saves it next to
    this script. Example csv_data: 'Name,Amount\\nRent,15000\\nGroceries,4000'"""
    if not OPENPYXL_AVAILABLE:
        return "Excel support not installed (pip install openpyxl)."
    try:
        import csv as _csv
        import io as _io
        clean_name = re.sub(r"[^a-zA-Z0-9_\- ]", "", filename.strip()) or "jarvis_report"
        if not clean_name.lower().endswith(".xlsx"):
            clean_name += ".xlsx"
        base_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base_dir, clean_name)
        rows = list(_csv.reader(_io.StringIO(csv_data.strip())))
        if not rows:
            return "No data provided to write."
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Report"
        for r in rows:
            ws.append(r)
        for col in ws.columns:
            width = max((len(str(c.value)) for c in col if c.value is not None), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(40, width + 2)
        wb.save(path)
        return f"Excel file created: {path}"
    except Exception as e:
        return f"Excel Error: {e}"

# ---- UI Automation (safer alternative to pixel-guessing) ----
def click_ui_button(window_title: str, control_name: str) -> str:
    """Finds a window by (partial) title and clicks a named button/control
    inside it, using the real Windows accessibility tree (Windows UI
    Automation) instead of guessing screen coordinates — much more
    reliable for standard Win32/WPF/UWP apps. Always confirmed with the
    user first."""
    if not is_system_automation_enabled():
        return "⚠️ System-level automation is disabled. Turn it on in Settings → Permissions."
    if not UIAUTOMATION_AVAILABLE:
        return "UI Automation library not installed (pip install uiautomation)."
    if not request_confirmation("Confirm UI Action", f"Click '{control_name}' in window '{window_title}'?"):
        return "Action cancelled — you didn't approve it."
    try:
        win = _auto.WindowControl(searchDepth=1, SubName=window_title)
        if not win.Exists(3):
            return f"Window containing '{window_title}' not found. Make sure it's open."
        win.SetActive()
        time.sleep(0.3)
        ctrl = win.Control(searchDepth=8, Name=control_name)
        if not ctrl.Exists(2):
            return f"Couldn't find a control named '{control_name}' in '{window_title}'."
        ctrl.Click(simulateMove=False)
        return f"Clicked '{control_name}' in '{window_title}'."
    except Exception as e:
        return f"UI Automation Error: {e}"

def type_in_ui_field(window_title: str, control_name: str, text: str) -> str:
    """Finds a named text field inside a window (via Windows UI Automation)
    and types into it. Always confirmed with the user first."""
    if not is_system_automation_enabled():
        return "⚠️ System-level automation is disabled. Turn it on in Settings → Permissions."
    if not UIAUTOMATION_AVAILABLE:
        return "UI Automation library not installed (pip install uiautomation)."
    if not request_confirmation("Confirm UI Action", f"Type into '{control_name}' in window '{window_title}'?"):
        return "Action cancelled — you didn't approve it."
    try:
        win = _auto.WindowControl(searchDepth=1, SubName=window_title)
        if not win.Exists(3):
            return f"Window containing '{window_title}' not found."
        win.SetActive()
        time.sleep(0.3)
        ctrl = win.Control(searchDepth=8, Name=control_name)
        if not ctrl.Exists(2):
            return f"Couldn't find a field named '{control_name}' in '{window_title}'."
        ctrl.Click(simulateMove=False)
        _auto.SendKeys(text)
        return f"Typed into '{control_name}' in '{window_title}'."
    except Exception as e:
        return f"UI Automation Error: {e}"

# ---- Self-Evolution: AI-proposed, human-approved custom tools ----
CUSTOM_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_tools")
CUSTOM_TOOLS_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_tools_registry.json")
custom_tools_runtime = {}  # name -> callable, loaded at startup + whenever a new one is approved

def _load_one_custom_tool(name: str):
    path = os.path.join(CUSTOM_TOOLS_DIR, f"{name}.py")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
        namespace = {}
        exec(code, namespace)
        fn = namespace.get(name)
        if callable(fn):
            custom_tools_runtime[name] = fn
    except Exception as e:
        log_error(f"Failed to load custom tool '{name}': {e}")

def load_custom_tools():
    """Loads previously-approved custom tools from disk at startup, so
    tools JARVIS built in past sessions stay available."""
    if not os.path.exists(CUSTOM_TOOLS_REGISTRY_PATH):
        return
    try:
        with open(CUSTOM_TOOLS_REGISTRY_PATH, "r", encoding="utf-8") as f:
            registry = json.load(f)
        for entry in registry:
            _load_one_custom_tool(entry["name"])
    except Exception as e:
        log_error(f"Failed to load custom tools registry: {e}")

def _save_registry_entry(name: str, description: str):
    registry = []
    if os.path.exists(CUSTOM_TOOLS_REGISTRY_PATH):
        try:
            with open(CUSTOM_TOOLS_REGISTRY_PATH, "r", encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            registry = []
    registry = [e for e in registry if e.get("name") != name]
    registry.append({"name": name, "description": description, "created_at": time.time()})
    with open(CUSTOM_TOOLS_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)

def propose_new_tool(tool_name: str, description: str, python_code: str) -> str:
    """Call this ONLY when no existing tool can do what the user asked.
    Write a single top-level Python function named exactly `tool_name`,
    with type-hinted string parameters and a docstring, that returns a
    string result. The code is shown to the user for review and is NEVER
    run without their explicit approval — then it's sandbox-tested (12s
    timeout) before being permanently registered."""
    if not is_system_automation_enabled():
        return "⚠️ Self-writing tools require System Automation permission (Settings → Permissions)."
    clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name.strip())[:40] or "custom_tool"
    approved, edited_code = request_code_approval(clean_name, description, python_code)
    if not approved:
        return "Tool creation cancelled — the generated code wasn't approved."
    try:
        os.makedirs(CUSTOM_TOOLS_DIR, exist_ok=True)
        test_result = subprocess.run([sys.executable, "-c", edited_code],
                                      capture_output=True, text=True, timeout=12)
        if test_result.returncode != 0 and test_result.stderr:
            return f"Tool test failed, not registered:\n{test_result.stderr[:400]}"
    except subprocess.TimeoutExpired:
        return "Tool test timed out (12s) — not registered."
    except Exception as e:
        return f"Tool test error: {e}"
    try:
        path = os.path.join(CUSTOM_TOOLS_DIR, f"{clean_name}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(edited_code)
        _save_registry_entry(clean_name, description)
        _load_one_custom_tool(clean_name)
        if clean_name not in custom_tools_runtime:
            return "Tool saved but couldn't be loaded — check it defines a function matching its own name."
        return f"New tool '{clean_name}' created, tested, and is now available: {description}"
    except Exception as e:
        return f"Tool registration error: {e}"

# ---- Multi-Agent Swarm: independent subtasks run in parallel ----
def spawn_parallel_agents(subtasks: str) -> str:
    """For a request with multiple INDEPENDENT parts (don't rely on each
    other's results), split it into subtasks (separated by ';' or newline)
    and run each with its own AI call in parallel, then return a combined
    report. Example: 'Check BTC price;Create an excel budget sheet;
    Schedule a meeting tomorrow 10am'. Do NOT use this for sequential/
    dependent steps — use normal reasoning for those instead."""
    parts = [p.strip() for p in re.split(r"[;\n]", subtasks) if p.strip()]
    if not parts:
        return "No subtasks provided."
    if len(parts) == 1:
        return process_voice_command(parts[0])
    from concurrent.futures import ThreadPoolExecutor
    results = {}

    def _run_one(item):
        idx, part = item
        results[idx] = process_voice_command(part)

    with ThreadPoolExecutor(max_workers=min(4, len(parts))) as ex:
        list(ex.map(_run_one, list(enumerate(parts))))
    lines = [f"[Agent {i + 1}: {parts[i][:40]}] → {results.get(i, '(no result)')}" for i in range(len(parts))]
    return "Parallel task swarm complete:\n\n" + "\n\n".join(lines)

GOOGLE_CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
GOOGLE_TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/youtube.readonly",
]
_google_creds = None

def google_is_connected() -> bool:
    return os.path.exists(GOOGLE_TOKEN_PATH)

def google_get_credentials():
    """Returns valid Google OAuth credentials, running the interactive
    consent flow (opens a browser) only the first time, or if the saved
    token can't be refreshed. This is a BLOCKING call — always run it on a
    background thread (e.g. via run_bg), never on the GUI thread."""
    global _google_creds
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError("Google API libraries not installed. Run: pip install google-auth "
                            "google-auth-oauthlib google-auth-httplib2 google-api-python-client")
    creds = _google_creds
    if creds and creds.valid:
        return creds
    if os.path.exists(GOOGLE_TOKEN_PATH):
        try:
            creds = _GoogleCredentials.from_authorized_user_file(GOOGLE_TOKEN_PATH, GOOGLE_SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(_GoogleAuthRequest())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not os.path.exists(GOOGLE_CREDENTIALS_PATH):
            raise RuntimeError(f"credentials.json not found. Download it from Google Cloud Console "
                                f"(OAuth client, Desktop app type) and place it here: {GOOGLE_CREDENTIALS_PATH}")
        flow = _GoogleInstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_PATH, GOOGLE_SCOPES)
        creds = flow.run_local_server(port=0)
        with open(GOOGLE_TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    _google_creds = creds
    return creds

def google_connect() -> str:
    """Triggers (or confirms) the OAuth connection. Call on a background thread."""
    try:
        google_get_credentials()
        return "Google account connected."
    except Exception as e:
        return f"Google Connect Error: {e}"

def google_disconnect() -> str:
    global _google_creds
    _google_creds = None
    try:
        if os.path.exists(GOOGLE_TOKEN_PATH):
            os.remove(GOOGLE_TOKEN_PATH)
        return "Disconnected from Google. Reconnect anytime from Settings."
    except Exception as e:
        return f"Disconnect Error: {e}"

def gmail_list_recent(count: str = "5") -> str:
    """Lists the subject and sender of the most recent Gmail messages."""
    try:
        n = max(1, min(int(str(count).strip() or 5), 15))
        creds = google_get_credentials()
        service = _google_build("gmail", "v1", credentials=creds)
        results = service.users().messages().list(userId="me", maxResults=n).execute()
        messages = results.get("messages", [])
        if not messages:
            return "No recent emails found."
        out = ["Recent Emails:"]
        for m in messages:
            msg = service.users().messages().get(userId="me", id=m["id"], format="metadata",
                                                   metadataHeaders=["Subject", "From"]).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subj = headers.get("Subject", "(no subject)")
            frm = headers.get("From", "unknown sender")
            out.append(f"• {subj} — from {frm}")
        return "\n".join(out)
    except Exception as e:
        return f"Gmail Error: {e}"

def gmail_send_email(to: str, subject: str, body: str) -> str:
    """Sends an email via the user's connected Gmail account."""
    try:
        import base64
        from email.mime.text import MIMEText
        creds = google_get_credentials()
        service = _google_build("gmail", "v1", credentials=creds)
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Email sent to {to}."
    except Exception as e:
        return f"Gmail Send Error: {e}"

def drive_list_recent_files(count: str = "8") -> str:
    """Lists recently modified files in the user's Google Drive."""
    try:
        n = max(1, min(int(str(count).strip() or 8), 20))
        creds = google_get_credentials()
        service = _google_build("drive", "v3", credentials=creds)
        results = service.files().list(pageSize=n, orderBy="modifiedTime desc",
                                        fields="files(id, name, modifiedTime, webViewLink)").execute()
        files = results.get("files", [])
        if not files:
            return "No files found in Drive."
        out = ["Recent Drive Files:"]
        for f in files:
            out.append(f"• {f['name']} — {f.get('webViewLink', '')}")
        return "\n".join(out)
    except Exception as e:
        return f"Drive Error: {e}"

def drive_upload_file(file_path: str) -> str:
    """Uploads a local file to the user's Google Drive."""
    try:
        creds = google_get_credentials()
        service = _google_build("drive", "v3", credentials=creds)
        file_metadata = {"name": os.path.basename(file_path)}
        media = _GoogleMediaFileUpload(file_path, resumable=True)
        f = service.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()
        return f"Uploaded to Drive: {f.get('webViewLink', f.get('id'))}"
    except Exception as e:
        return f"Drive Upload Error: {e}"

def docs_create_document(title: str, content: str = "") -> str:
    """Creates a new Google Doc with the given title and starting text content."""
    try:
        creds = google_get_credentials()
        service = _google_build("docs", "v1", credentials=creds)
        doc = service.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId")
        if content:
            service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]}
            ).execute()
        return f"Created Google Doc '{title}': https://docs.google.com/document/d/{doc_id}/edit"
    except Exception as e:
        return f"Docs Error: {e}"

def calendar_list_events(days: str = "1") -> str:
    """Lists upcoming Google Calendar events over the next N days (default: today/tomorrow window)."""
    try:
        n_days = max(1, min(int(str(days).strip() or 1), 30))
        creds = google_get_credentials()
        service = _google_build("calendar", "v3", credentials=creds)
        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=n_days)).isoformat() + "Z"
        results = service.events().list(calendarId="primary", timeMin=time_min, timeMax=time_max,
                                          singleEvents=True, orderBy="startTime", maxResults=15).execute()
        events = results.get("items", [])
        if not events:
            return f"No events found in the next {n_days} day(s)."
        out = [f"Upcoming events (next {n_days} day(s)):"]
        for e in events:
            start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "?"))
            out.append(f"• {e.get('summary', '(no title)')} — {start}")
        return "\n".join(out)
    except Exception as e:
        return f"Calendar Error: {e}"

def calendar_create_event(title: str, start_datetime: str, duration_minutes: str = "60") -> str:
    """Creates a Google Calendar event. start_datetime like '2026-09-15 17:00'
    or '2026-09-15T17:00:00'. Uses your calendar's default time zone."""
    try:
        creds = google_get_credentials()
        service = _google_build("calendar", "v3", credentials=creds)
        dt_str = start_datetime.strip().replace(" ", "T")
        if len(dt_str) == 16:
            dt_str += ":00"
        start_dt = datetime.fromisoformat(dt_str)
        end_dt = start_dt + timedelta(minutes=max(5, int(str(duration_minutes).strip() or 60)))
        event = {"summary": title, "start": {"dateTime": start_dt.isoformat()},
                 "end": {"dateTime": end_dt.isoformat()}}
        created = service.events().insert(calendarId="primary", body=event).execute()
        return f"Event '{title}' created for {start_dt.strftime('%b %d, %Y %I:%M %p')}. Link: {created.get('htmlLink')}"
    except Exception as e:
        return f"Calendar Create Error: {e}"

def get_upcoming_events_structured(minutes_ahead: int = 15):
    """Internal helper (not an AI tool) for the proactive-alert watchdog —
    returns [(event_id, summary, start_iso_string), ...] for events
    starting within the next `minutes_ahead` minutes. Silently returns []
    if Google isn't connected or anything goes wrong, so the periodic
    check never disrupts the app."""
    try:
        if not (GOOGLE_LIBS_AVAILABLE and google_is_connected()):
            return []
        creds = google_get_credentials()
        service = _google_build("calendar", "v3", credentials=creds)
        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(minutes=minutes_ahead)).isoformat() + "Z"
        results = service.events().list(calendarId="primary", timeMin=time_min, timeMax=time_max,
                                          singleEvents=True, orderBy="startTime", maxResults=10).execute()
        out = []
        for e in results.get("items", []):
            start_raw = e.get("start", {}).get("dateTime")
            if not start_raw:
                continue  # skip all-day events for proactive alerts
            out.append((e.get("id"), e.get("summary", "(no title)"), start_raw))
        return out
    except Exception:
        return []

def format_event_time(start_raw: str) -> str:
    try:
        dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%I:%M %p")
    except Exception:
        return start_raw

def generate_ai_image(prompt: str) -> str:
    """Generates an image from a text prompt using Gemini's native image
    model, saves it locally, and returns an IMG_FILE: marker so the chat UI
    can preview it inline (same pattern as the QR/screenshot markers).
    Uses the same key pool + automatic failover as chat."""
    global gemini_key_index
    if not GEMINI_KEYS:
        return "⚠️ No Gemini API key configured. Add one in Settings → API Keys."
    keys_snapshot = list(GEMINI_KEYS)
    last_err = "Model returned no image data."
    for attempt in range(len(keys_snapshot)):
        current_key = keys_snapshot[gemini_key_index % len(keys_snapshot)]
        try:
            client_instance = genai.Client(api_key=current_key)
            res = client_instance.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["Text", "Image"]),
            )
            parts = res.candidates[0].content.parts if res.candidates else []
            for part in parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    base_dir = os.path.dirname(os.path.abspath(__file__))
                    img_path = os.path.join(base_dir, f"jarvis_image_{int(time.time())}.png")
                    with open(img_path, "wb") as f:
                        f.write(part.inline_data.data)
                    return f"IMG_FILE:{img_path}"
        except Exception as e:
            last_err = str(e)
        gemini_key_index = (gemini_key_index + 1) % len(keys_snapshot)
    low = last_err.lower()
    if "resource_exhausted" in low or "429" in low or "quota" in low:
        if "limit: 0" in low:
            return ("🖼️ Image generation isn't available on the free Gemini API tier (its image quota is 0 for free keys). "
                    "To use it, enable billing on your Google AI Studio project/key — or skip images; "
                    "everything else (chat, Gmail, Drive, voice…) keeps working on the free tier.")
        return ("🖼️ Image generation quota is used up on all your keys right now. "
                "Please try again later, or add another key in Settings → API Keys.")
    return f"Image Generation Error: {last_err[:300]}"

# ==========================================
# 4b. WEB & APP AUTOMATION
#     Chrome DevTools Protocol (CDP) + deterministic "recipes" + guarded AI fallback agent
# ==========================================
# How it works (kept deliberately cheap and safe):
#   * JARVIS starts a SEPARATE Chrome window with its own profile folder
#     (jarvis_chrome_profile) and a local-only debugging port. Your normal Chrome
#     profile, bookmarks and passwords are never touched.
#   * Known sites use fixed recipes (plain Python, ZERO extra AI calls).
#   * Unknown/multi-step tasks use web_agent(): few AI calls, several actions per call,
#     hard step limits, confirmation before anything risky (pay / place order / delete),
#     and it never types passwords, OTPs or card numbers.
CDP_PORT = 9222
WEB_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_chrome_profile")
_cdp_proc = {"p": None}

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False

def _cdp_alive() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False

def _find_browser_exe():
    import shutil
    env = os.environ.get("JARVIS_CHROME")
    if env and os.path.exists(env):
        return env
    pf, pf86, local = (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                       os.environ.get("LOCALAPPDATA", ""))
    cands = [os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
             os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
             os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
             os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
             os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        w = shutil.which(name)
        if w:
            return w
    return None

def ensure_cdp_browser():
    """Returns (ok, error). Starts JARVIS's own Chrome window if it isn't running."""
    if _cdp_alive():
        return True, ""
    exe = _find_browser_exe()
    if not exe:
        return False, "Google Chrome (or Edge) was not found on this PC."
    try:
        os.makedirs(WEB_PROFILE_DIR, exist_ok=True)
        args = [exe, f"--remote-debugging-port={CDP_PORT}", "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={WEB_PROFILE_DIR}", "--no-first-run", "--no-default-browser-check"]
        extra = os.environ.get("JARVIS_CHROME_ARGS", "").split()
        _cdp_proc["p"] = subprocess.Popen(args + extra, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, f"Could not start Chrome: {e}"
    for _ in range(40):
        if _cdp_alive():
            return True, ""
        time.sleep(0.5)
    return False, "Chrome started but its automation port did not respond."

def shutdown_cdp_browser():
    """Closes only the Chrome window JARVIS itself started (frees the debugging port)."""
    p = _cdp_proc.get("p")
    try:
        if p and p.poll() is None:
            p.terminate()
    except Exception:
        pass

import atexit
atexit.register(shutdown_cdp_browser)

class _WebSession:
    """Connect to JARVIS's Chrome over CDP for the duration of one task, then disconnect
    (disconnecting does not close the browser)."""
    def __enter__(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Web automation needs Playwright: pip install playwright")
        ok, err = ensure_cdp_browser()
        if not ok:
            raise RuntimeError(err)
        self.pw = sync_playwright().start()
        try:
            self.browser = self.pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
            self.page = self.ctx.new_page()
            try:
                self.page.bring_to_front()
            except Exception:
                pass
        except Exception:
            self.pw.stop()
            raise
        return self
    def __exit__(self, *exc):
        try:
            self.pw.stop()
        except Exception:
            pass
        return False

# ---- Recipes: fixed search URLs for popular sites (no AI, no clicking needed) ----
SEARCH_SITES = {
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "amazon": "https://www.amazon.in/s?k={q}",
    "flipkart": "https://www.flipkart.com/search?q={q}",
    "meesho": "https://www.meesho.com/search?q={q}",
    "myntra": "https://www.myntra.com/{qd}?rawQuery={q}",
    "ajio": "https://www.ajio.com/search/?text={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "github": "https://github.com/search?q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "imdb": "https://www.imdb.com/find/?q={q}",
    "spotify": "https://open.spotify.com/search/{q}",
    "gmail": "https://mail.google.com/mail/u/0/#search/{q}",
    "drive": "https://drive.google.com/drive/search?q={q}",
    "twitter": "https://x.com/search?q={q}",
    "x": "https://x.com/search?q={q}",
    "linkedin": "https://www.linkedin.com/search/results/all/?keywords={q}",
    "olx": "https://www.olx.in/items/q-{qd}",
    "bing": "https://www.bing.com/search?q={q}",
}

def _norm_url(url: str):
    from urllib.parse import urlparse
    u = (url or "").strip()
    if not u:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:(?!\d)", u) and not re.match(r"^https?://", u, re.I):
        return None          # javascript:, file:, data:, etc. are never opened
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    pr = urlparse(u)
    if pr.scheme not in ("http", "https") or not pr.netloc:
        return None
    return u

def _web_goto(url: str, wait_after: float = 0.5) -> str:
    with _WebSession() as s:
        s.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(wait_after)
        return s.page.title() or url

def open_website(url: str) -> str:
    """Opens a website (http/https URL or a bare domain like 'irctc.co.in') in JARVIS's automation Chrome window."""
    u = _norm_url(url)
    if not u:
        return "That doesn't look like a valid website address."
    try:
        title = _web_goto(u)
        return f"DONE: Opened {u} — page title: “{title}”."
    except Exception as e:
        log_error(f"open_website: {e}")
        try:
            webbrowser.open(u)
            return f"DONE: Opened {u} in your default browser (automation browser unavailable: {e})."
        except Exception:
            return f"Could not open {u}: {e}"

def web_search(site: str, query: str) -> str:
    """Searches a popular site and leaves the results page open in the browser. site is one of: google, youtube, amazon, flipkart, meesho, myntra, ajio, maps, wikipedia, github, reddit, imdb, spotify, gmail, drive, twitter, linkedin, olx, bing. Use this for simple 'search X on Y' requests — it needs no extra AI steps."""
    from urllib.parse import quote_plus
    key = (site or "").strip().lower().replace(".com", "").replace(".in", "").replace("www.", "")
    key = {"yt": "youtube", "gmaps": "maps", "google maps": "maps", "amazon.in": "amazon", "insta": "google"}.get(key, key)
    if key not in SEARCH_SITES:
        return (f"I don't have a fixed recipe for '{site}'. Known: {', '.join(sorted(SEARCH_SITES))}. "
                f"Try web_agent for other websites.")
    q = (query or "").strip()
    url = SEARCH_SITES[key].format(q=quote_plus(q), qd=quote_plus(q.lower()).replace("+", "-"))
    try:
        title = _web_goto(url, 1.0)
        return f"DONE: Opened {key.title()} and searched “{q}”. The results page is open in the browser (“{title}”)."
    except Exception as e:
        log_error(f"web_search: {e}")
        return f"Web search failed: {e}"

# ---- Guarded AI fallback agent (few AI calls, many actions per call) ----
WEB_RISKY_RE = re.compile(r"\b(pay(ment)?|place (your )?order|buy now|confirm (order|booking|payment|purchase)|"
                          r"book(ing)? now|proceed to pay|complete (purchase|order)|submit order|"
                          r"delete|remove account|send money|transfer|donate|subscribe)\b", re.I)
WEB_SECRET_RE = re.compile(r"(password|passcode|otp|one[- ]time|cvv|cvc|card number|pin\b|security code)", re.I)

_SNAPSHOT_JS = """
() => {
  const sel = 'a,button,input,select,textarea,[role=button],[role=link],[role=checkbox],[role=tab],[role=menuitem],[role=option],[onclick]';
  document.querySelectorAll('[data-jv]').forEach(e => e.removeAttribute('data-jv'));
  const out = []; let id = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    if (r.width < 4 || r.height < 4 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.bottom < 0 || r.top > innerHeight * 2.5) continue;
    el.setAttribute('data-jv', String(id));
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || el.placeholder || el.title || el.alt || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
    out.push({id: id, tag: el.tagName.toLowerCase(), type: el.type || '', label: label, ph: el.placeholder || '',
              href: el.tagName === 'A' ? (el.getAttribute('href') || '').slice(0, 50) : ''});
    id++; if (id >= 70) break;
  }
  return {url: location.href, title: document.title, elements: out,
          text: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, 1200)};
}
"""

def _gemini_json(prompt: str):
    """One JSON-returning Gemini call with the same multi-key failover as chat."""
    global gemini_key_index
    if not GEMINI_KEYS:
        return None
    keys = list(GEMINI_KEYS)
    for _ in range(len(keys)):
        try:
            client_instance = genai.Client(api_key=keys[gemini_key_index % len(keys)])
            res = client_instance.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1))
            txt = (res.text or "").strip()
            txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
            return json.loads(txt)
        except Exception as e:
            log_error(f"[web agent gemini]: {e}")
        gemini_key_index = (gemini_key_index + 1) % len(keys)
    return None

def web_agent(goal: str, start_url: str = "") -> str:
    """Carries out a multi-step task on ANY website (fill a form, add to cart, navigate menus, look something up) by looking at the page and acting. Slower and uses a few AI calls, so for a plain 'search X on Y' use web_search instead. Stops and asks the user for logins/OTP/CAPTCHA and before any payment or order confirmation."""
    if not is_system_automation_enabled():
        return "⚠️ The web agent clicks and types in a browser for you, so it needs permission. Turn on Settings → Permissions, then ask again."
    MAX_ROUNDS, MAX_ACTIONS = 8, 28
    history, acted = [], 0
    try:
        with _WebSession() as s:
            page = s.page
            u = _norm_url(start_url) if start_url else None
            if u:
                page.goto(u, wait_until="domcontentloaded", timeout=30000)
            for rnd in range(MAX_ROUNDS):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                snap = page.evaluate(_SNAPSHOT_JS)
                els = {e["id"]: e for e in snap["elements"]}
                lines = "\n".join(f"[{e['id']}] {e['tag']}{('('+e['type']+')') if e['type'] else ''} "
                                  f"'{e['label']}'{(' placeholder='+e['ph']) if e['ph'] else ''}" for e in snap["elements"])
                prompt = (
                    f"You operate a web browser to achieve this goal: {goal}\n\n"
                    f"Current page: {snap['url']}  (title: {snap['title']})\n"
                    f"Visible text (start): {snap['text']}\n\nInteractive elements:\n{lines}\n\n"
                    f"Actions taken so far: {history[-8:] if history else 'none'}\n\n"
                    'Reply with ONLY JSON: {"thought":"short","actions":[...]} where each action is one of:\n'
                    '{"do":"click","id":N} | {"do":"type","id":N,"text":"...","enter":true|false} | '
                    '{"do":"goto","url":"https://..."} | {"do":"press","key":"Enter"} | {"do":"scroll","dir":"down"} | '
                    '{"do":"wait","sec":2} | {"do":"ask_user","message":"..."} | {"do":"done","summary":"..."} | '
                    '{"do":"fail","reason":"..."}\n'
                    "Rules: at most 4 actions per reply; put several only when they are all on THIS page "
                    "(stop the list after a click that changes the page). Never type passwords, OTPs, card numbers — "
                    "use ask_user so the person does it. Use ask_user for logins and CAPTCHAs. Say done only if the goal "
                    "is visibly complete on the page. If the goal cannot be done, use fail.")
                plan = _gemini_json(prompt)
                if not plan or not isinstance(plan.get("actions"), list) or not plan["actions"]:
                    return "I couldn't work out the next step on this page (the AI gave no usable plan)."
                for act in plan["actions"][:4]:
                    acted += 1
                    if acted > MAX_ACTIONS:
                        return "Stopped: too many steps for one task. Here is where I got to — the page is open in the browser."
                    do = str(act.get("do", "")).lower()
                    try:
                        if do == "done":
                            return "DONE: " + str(act.get("summary", "Task completed."))
                        if do == "fail":
                            return "I couldn't complete that: " + str(act.get("reason", "unknown reason"))
                        if do == "ask_user":
                            msg = str(act.get("message", "Please take over in the browser."))
                            if not request_confirmation("Action needed in the browser",
                                                        msg + "\n\nDo it in the JARVIS browser window, then press Yes to continue (No to stop).",
                                                        timeout=300):
                                return "Stopped — you chose not to continue."
                            history.append(f"user handled: {msg[:60]}")
                            break
                        if do == "goto":
                            nu = _norm_url(str(act.get("url", "")))
                            if not nu:
                                continue
                            page.goto(nu, wait_until="domcontentloaded", timeout=30000)
                            history.append(f"goto {nu[:60]}")
                            break
                        if do == "scroll":
                            page.mouse.wheel(0, 700 if act.get("dir", "down") == "down" else -700)
                            history.append("scroll")
                            time.sleep(0.4)
                            break
                        if do == "wait":
                            time.sleep(min(float(act.get("sec", 2)), 6))
                            continue
                        if do == "press":
                            page.keyboard.press(str(act.get("key", "Enter")))
                            history.append(f"press {act.get('key')}")
                            time.sleep(0.8)
                            break
                        if do in ("click", "type"):
                            e = els.get(int(act.get("id", -1)))
                            if not e:
                                history.append("bad element id")
                                break
                            label = e["label"] or e["ph"] or e["tag"]
                            loc = page.locator(f'[data-jv="{e["id"]}"]').first
                            if do == "type":
                                if e["type"] == "password" or WEB_SECRET_RE.search(f"{e['label']} {e['ph']}"):
                                    if not request_confirmation("Action needed in the browser",
                                                                f"Please type “{label}” yourself in the browser (I never enter passwords, OTPs or card details), "
                                                                "then press Yes to continue.", timeout=300):
                                        return "Stopped — you chose not to continue."
                                    history.append(f"user typed {label[:30]}")
                                    break
                                loc.click(timeout=5000)
                                loc.fill(str(act.get("text", "")), timeout=5000)
                                if act.get("enter"):
                                    page.keyboard.press("Enter")
                                history.append(f"typed into {label[:30]}")
                                time.sleep(0.6)
                                if act.get("enter"):
                                    break
                                continue
                            # click
                            if WEB_RISKY_RE.search(label):
                                host = re.sub(r"^https?://", "", snap["url"]).split("/")[0]
                                if not request_confirmation("Confirm Web Action",
                                                            f"JARVIS wants to click “{label}” on {host}.\n\nThis may spend money or change something. Allow?"):
                                    return f"Stopped before clicking “{label}” — you didn't approve it."
                            before = len(s.ctx.pages)
                            loc.click(timeout=6000)
                            time.sleep(0.8)
                            if len(s.ctx.pages) > before:
                                page = s.ctx.pages[-1]
                                try:
                                    page.bring_to_front()
                                except Exception:
                                    pass
                            history.append(f"clicked {label[:30]}")
                            break
                    except Exception as act_err:
                        history.append(f"{do} failed: {str(act_err)[:60]}")
                        break
            return "I worked on it but couldn't confirm it finished — the page is open in the browser so you can check or continue."
    except Exception as e:
        log_error(f"web_agent: {e}")
        return f"Web agent error: {e}"

# ---- PC apps: known launchers + whitelisted deep links (no AI guessing of commands) ----
APP_LAUNCHERS = {
    "whatsapp": ("uri", "whatsapp:"), "spotify": ("uri", "spotify:"), "telegram": ("uri", "tg:"),
    "discord": ("uri", "discord:"), "teams": ("uri", "msteams:"), "zoom": ("uri", "zoommtg:"),
    "steam": ("uri", "steam:"), "settings": ("uri", "ms-settings:"), "wifi": ("uri", "ms-settings:network-wifi"),
    "bluetooth": ("uri", "ms-settings:bluetooth"), "display settings": ("uri", "ms-settings:display"),
    "sound settings": ("uri", "ms-settings:sound"), "windows update": ("uri", "ms-settings:windowsupdate"),
    "camera": ("uri", "microsoft.windows.camera:"), "store": ("uri", "ms-windows-store:"),
    "clock": ("uri", "ms-clock:"), "photos": ("uri", "ms-photos:"), "snipping tool": ("uri", "ms-screenclip:"),
    "mail": ("uri", "mailto:"), "maps": ("uri", "bingmaps:"),
    "calculator": ("exe", "calc.exe"), "notepad": ("exe", "notepad.exe"), "paint": ("exe", "mspaint.exe"),
    "task manager": ("exe", "taskmgr.exe"), "file explorer": ("exe", "explorer.exe"), "explorer": ("exe", "explorer.exe"),
    "command prompt": ("exe", "cmd.exe"), "cmd": ("exe", "cmd.exe"), "powershell": ("exe", "powershell.exe"),
    "terminal": ("exe", "wt.exe"), "control panel": ("exe", "control.exe"), "word": ("exe", "winword.exe"),
    "excel": ("exe", "excel.exe"), "powerpoint": ("exe", "powerpnt.exe"), "vs code": ("exe", "code"),
    "vscode": ("exe", "code"), "chrome": ("exe", "chrome.exe"), "edge": ("exe", "msedge.exe"), "vlc": ("exe", "vlc.exe"),
}
SAFE_URI_SCHEMES = {"whatsapp", "spotify", "tg", "discord", "mailto", "tel", "msteams", "zoommtg", "steam",
                    "ms-settings", "ms-clock", "ms-photos", "ms-screenclip", "microsoft.windows.camera",
                    "ms-windows-store", "bingmaps", "skype"}

def _launch_uri(uri: str):
    if hasattr(os, "startfile"):
        os.startfile(uri)
    else:
        raise RuntimeError("App launching is only supported on Windows.")

def open_app(app_name: str) -> str:
    """Opens a PC application or Windows settings page by name (e.g. 'whatsapp', 'spotify', 'calculator', 'notepad', 'bluetooth', 'wifi', 'vs code', 'word', 'task manager', 'camera')."""
    key = (app_name or "").strip().lower()
    hit = APP_LAUNCHERS.get(key)
    if not hit:
        for k, v in APP_LAUNCHERS.items():
            if k in key or key in k:
                hit, key = v, k
                break
    if not hit:
        return f"I don't have a launcher for '{app_name}'. I can open: {', '.join(sorted(APP_LAUNCHERS))}."
    kind, target = hit
    try:
        if kind == "uri":
            _launch_uri(target)
        else:
            subprocess.Popen(f'start "" "{target}"', shell=True)
        return f"DONE: Launched {key}."
    except Exception as e:
        return f"Could not open {key}: {e}"

def open_deep_link(uri: str) -> str:
    """Opens an app deep link such as whatsapp://send?phone=919999999999&text=hi , spotify:search:lofi , mailto:a@b.com , tel:+91..., ms-settings:display . Only well-known safe app schemes are allowed."""
    from urllib.parse import urlparse
    u = (uri or "").strip()
    scheme = urlparse(u).scheme.lower() or u.split(":", 1)[0].lower()
    if scheme not in SAFE_URI_SCHEMES:
        return f"Blocked: '{scheme}' links aren't on the safe list ({', '.join(sorted(SAFE_URI_SCHEMES))})."
    try:
        _launch_uri(u)
        return f"DONE: Opened {scheme} link."
    except Exception as e:
        return f"Could not open the link: {e}"


WEB_TOOLS = [open_website, web_search, web_agent, open_app, open_deep_link]

MODE_CONFIGS = {
    "General": {"badge": "💬 General", "tools": [get_system_status, remember_fact] + WEB_TOOLS,
                "system_prompt": "Act as a helpful, concise personal assistant. Answer clearly and directly. If the user tells you something worth remembering long-term (preferences, facts about them), call remember_fact."},
    "Research": {"badge": "📖 Research", "tools": [search_wikipedia, scrape_webpage, remember_fact],
                 "system_prompt": "Operate in Research mode. Provide clean, factual answers in structured bullet points, and use your tools to look things up when useful."},
    "Coder": {"badge": "</> Coder", "tools": [get_system_status, remember_fact],
              "system_prompt": "Operate in Coder mode. Always write code directly inside ```python (or the relevant language) code blocks so it can be run. Never claim you cannot write code."},
    "Creative": {"badge": "🎨 Creative", "tools": [remember_fact],
                 "system_prompt": "Operate in Creative mode. Write engaging, original creative content (stories, ideas, copy) tailored to the request."},
    "Automation": {"badge": "⚡ Automation",
                   "tools": [play_youtube_video, open_whatsapp, search_github, open_or_send_gmail, generate_qr_code,
                             scrape_webpage, search_wikipedia, take_screenshot, close_application, get_system_status,
                             set_system_volume, set_screen_brightness, set_reminder_timer,
                             get_crypto_price, convert_forex_currency, calculate_lot_size,
                             gmail_list_recent, gmail_send_email, drive_list_recent_files,
                             drive_upload_file, docs_create_document, generate_ai_image,
                             calendar_list_events, calendar_create_event, remember_fact,
                             run_shell_command, create_excel_file, click_ui_button, type_in_ui_field,
                             propose_new_tool, spawn_parallel_agents] + WEB_TOOLS,
                   "system_prompt": "Operate in Task Automation mode. Execute automation tools, control hardware parameters, or answer queries directly. If the user tells you something worth remembering long-term, call remember_fact."},
}
active_mode_key = "General"
ALL_TOOLS = [
    play_youtube_video, open_whatsapp, search_github, open_or_send_gmail, generate_qr_code, scrape_webpage,
    search_wikipedia, take_screenshot, close_application, get_system_status, get_crypto_price,
    convert_forex_currency, calculate_lot_size, set_system_volume, set_screen_brightness, set_reminder_timer,
    gmail_list_recent, gmail_send_email, drive_list_recent_files, drive_upload_file, docs_create_document,
    generate_ai_image, calendar_list_events, calendar_create_event, remember_fact,
    run_shell_command, create_excel_file, click_ui_button, type_in_ui_field,
    propose_new_tool, spawn_parallel_agents,
    open_website, web_search, web_agent, open_app, open_deep_link,
]

# ==========================================
# 5. THREAD-SAFE UI BRIDGE
# ==========================================
class Bridge(QObject):
    """All background threads emit through this instead of touching widgets
    directly — Qt automatically queues cross-thread signal delivery onto the
    main/GUI thread, which is the safe way to do this (unlike calling widget
    methods straight from a worker thread, which is undefined behaviour)."""
    chat_status = pyqtSignal(str)
    chat_done = pyqtSignal(str, bool)          # (reply_text, ok)
    voice_captured = pyqtSignal(str)
    tool_result = pyqtSignal(str, str)         # (target_widget_key, result_text)
    hotkey_toggle = pyqtSignal()               # emitted from the background hotkey-listener thread
    wake_word_detected = pyqtSignal(str, str)  # (persona name, command spoken after the wake word)
    confirmation_requested = pyqtSignal(str, str, object, object)   # (title, message, result_holder, threading.Event)
    code_approval_requested = pyqtSignal(str, str, str, object, object)  # (tool_name, description, code, result_holder, threading.Event)
    toast = pyqtSignal(str, str)               # (message, kind) — kind: info/success/error
    reminder_fired = pyqtSignal(str)           # emitted from a reminder's background timer thread

bridge = Bridge()

def notify(message: str, kind: str = "info"):
    """Thread-safe: logs to jarvis.log AND shows an in-app toast. Call this
    instead of a bare print() for anything the user should actually see —
    print() output is invisible once packaged as a --windowed .exe."""
    (log_error if kind == "error" else log_info)(message)
    bridge.toast.emit(message, kind)

def request_confirmation(title: str, message: str, timeout: float = 60.0) -> bool:
    """Thread-safe: shows a Yes/No confirmation dialog on the GUI thread and
    BLOCKS the calling (background) thread until the user responds, or
    returns False (fail-safe) if they don't respond within `timeout`
    seconds. Used before any risky system-level action (shell commands,
    UI-automation clicks, etc.) — never skip this for destructive actions."""
    result_holder = {"confirmed": False}
    event = threading.Event()
    bridge.confirmation_requested.emit(title, message, result_holder, event)
    event.wait(timeout)
    return result_holder["confirmed"]

def request_code_approval(tool_name: str, description: str, code: str, timeout: float = 180.0):
    """Thread-safe: shows the AI-generated code to the user in a dialog and
    BLOCKS until they Approve or Reject (or the dialog times out, which
    counts as Reject — fail-safe). Never run AI-generated code without
    this. Returns (approved: bool, possibly-edited code: str)."""
    result_holder = {"approved": False, "code": code}
    event = threading.Event()
    bridge.code_approval_requested.emit(tool_name, description, code, result_holder, event)
    event.wait(timeout)
    return result_holder["approved"], result_holder["code"]

# ==========================================
# 5b. WAKE WORD ("Hey Jarvis")
# ==========================================
# No extra account/API key needed — reuses the SpeechRecognition package
# already required for the Voice page, listening in short bursts and
# checking the transcript for "jarvis". Honest trade-off vs a dedicated
# wake-word engine (e.g. Picovoice): higher latency, needs internet for
# each burst (Google's free STT), and is less precise — but zero extra
# setup. Off by default; toggle it on from Settings.
wake_word_state = {"enabled": False, "suspended": False}

def _wake_word_loop():
    if not sr:
        return
    r = sr.Recognizer()
    while True:
        if not wake_word_state["enabled"] or wake_word_state["suspended"]:
            time.sleep(1)
            continue
        try:
            with sr.Microphone() as source:
                r.adjust_for_ambient_noise(source, duration=0.4)
                audio = r.listen(source, timeout=5, phrase_time_limit=6)
            if not wake_word_state["enabled"] or wake_word_state["suspended"]:
                continue
            text = r.recognize_google(audio).lower()
            # whichever persona name appears first in the phrase wins
            hits = [(text.find(p["wake_word"]), pname, p["wake_word"])
                    for pname, p in PERSONAS.items() if p["wake_word"] in text]
            if hits:
                _, pname, word = min(hits)
                command = text.split(word, 1)[1].strip(" ,.")
                bridge.wake_word_detected.emit(pname, command)
        except Exception:
            time.sleep(0.5)
            continue

background_threads = {}  # name -> (Thread, target_fn) — used by the watchdog to detect & restart dead threads

def start_managed_thread(name: str, target):
    t = threading.Thread(target=target, daemon=True)
    t.start()
    background_threads[name] = (t, target)
    return t

start_managed_thread("wake_word", _wake_word_loop)

load_custom_tools()

# ==========================================
# 6. AUDIO (TTS) CONTROLLER
# ==========================================
def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

def _cleanup_old_voice_files():
    """Remove leftover voice_*.mp3 from older builds / crashes."""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        for fn in os.listdir(base):
            if fn.startswith("voice_") and fn.endswith(".mp3"):
                _safe_remove(os.path.join(base, fn))
    except Exception:
        pass

_cleanup_old_voice_files()

audio_state = {"is_playing": False, "current_btn": None, "task_id": 0}

_tts_warned = {"net": False}

def prepare_speech_text(text: str) -> str:
    """Make text sound natural when spoken: no emojis, URLs, markdown or code."""
    t = clean_display_text(text).replace("•", ", ")
    t = re.sub(r"```.*?```", " Code block omitted from audio. ", t, flags=re.DOTALL)
    t = re.sub(r"https?://\S+", " link ", t)
    t = re.sub(r"[`_~|#>]+", " ", t)
    t = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]", "", t)
    t = re.sub(r"\s*\n+\s*", ". ", t)
    return re.sub(r"\s{2,}", " ", t).strip()[:2500]

def synthesize_speech(text: str, out_path: str):
    """edge-tts with persona prosody; tries fallback voices if one is unavailable.
    Network failures propagate so the caller can use the offline voice."""
    names, rate, pitch = VOICE_PROFILES.get(CURRENT_PERSONA, VOICE_PROFILES["JARVIS"])
    if GREETING_LANGUAGE == "hindi":
        names, rate, pitch = HINDI_VOICES.get(CURRENT_PERSONA, ["hi-IN-SwaraNeural"]), "+0%", "+0Hz"
    last = None
    for v in names:
        try:
            asyncio.run(edge_tts.Communicate(text, v, rate=rate, pitch=pitch).save(out_path))
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return
        except Exception as e:
            last = e
            if "getaddrinfo" in str(e) or "Cannot connect" in str(e) or "timeout" in str(e).lower():
                break   # network problem — other voices won't help
    raise last or RuntimeError("TTS failed")

def _sapi_speak(text: str):
    """Offline fallback voice (built into Windows) used when edge-tts has no internet."""
    try:
        gender = "Male" if CURRENT_PERSONA == "JARVIS" else "Female"
        clean = prepare_speech_text(text).replace("'", " ").replace('"', " ")[:1500]
        ps = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"try{{$s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::{gender})}}catch{{}}; $s.Speak('{clean}')")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=120)
    except Exception as e:
        log_error(f"SAPI fallback error: {e}")

def speak_text_sync(text: str):
    """Blocking TTS playback for the conversation orb — not tied to any
    button/widget, just plays to completion (or fails silently). Stops any
    chat-bubble 'Listen' playback first so the two never overlap on the
    single pygame audio channel."""
    stop_voice()
    if not edge_tts or not pygame:
        return
    try:
        clean = prepare_speech_text(text)
        audio_file = os.path.join(tempfile.gettempdir(), f"jarvis_voice_{int(time.time()*1000)}.mp3")
        synthesize_speech(clean, audio_file)
        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass
        _safe_remove(audio_file)
    except Exception as e:
        if not _tts_warned["net"]:
            log_error(f"Orb TTS error (using offline voice; check internet/DNS): {e}")
            _tts_warned["net"] = True
        _sapi_speak(text)


# ==========================================
# 4c. FRIENDLY ERRORS, CONVERSATION CONTEXT, HEALTH CHECK
# ==========================================
def friendly_error(err) -> str:
    """Turns raw exceptions/API errors into one short, actionable line."""
    raw = str(err) if err is not None else ""
    low = raw.lower()
    if "resource_exhausted" in low or re.search(r"\b429\b", low) or "quota" in low or "rate limit" in low:
        if "limit: 0" in low and "image" in low:
            return ("⚠️ Image generation isn't available on the free Gemini tier. Enable billing on your Google AI Studio "
                    "project to use it — everything else keeps working.")
        return ("⚠️ Your Gemini quota/rate limit is used up on all saved keys. Wait a minute (per-minute limit) or until "
                "tomorrow (daily limit), or add another key in Settings → API Keys.")
    if "api key not valid" in low or "api_key_invalid" in low or "permission_denied" in low or re.search(r"\b(401|403)\b", low):
        return ("⚠️ Your Gemini API key was rejected. Check it in Settings → API Keys, or create a fresh one at "
                "aistudio.google.com/apikey.")
    if any(k in low for k in ("getaddrinfo", "name or service not known", "10060", "10061", "connection reset",
                              "cannot connect", "timed out", "timeout", "ssl", "connection error", "max retries",
                              "network is unreachable", "temporary failure in name resolution")):
        return ("⚠️ Can't reach the internet/Google right now (network or DNS problem). Check your Wi-Fi, VPN or DNS "
                "(try 8.8.8.8), then try again. Settings → Run Health Check shows what is blocked.")
    if re.search(r"\b503\b", low) or "overloaded" in low or "service unavailable" in low:
        return "⚠️ Gemini is busy right now. Please try again in a moment."
    if re.search(r"\b404\b", low) and "model" in low:
        return "⚠️ That Gemini model isn't available for your key. Try again later or update the app."
    if "safety" in low or "blocked" in low and "prompt" in low:
        return "⚠️ Gemini declined that request (safety filter). Try rephrasing it."
    m = re.search(r"no module named '([^']+)'", low)
    if m or "modulenotfounderror" in low:
        name = m.group(1) if m else "a package"
        return f"⚠️ A required Python package is missing ({name}). Run: pip install -r requirements.txt"
    if "invalid_grant" in low or "token has been expired" in low or "token has been revoked" in low:
        return "⚠️ Your Google login expired. Delete token.json and reconnect in Settings → Google."
    if "access is denied" in low or "winerror 5" in low or "permissionerror" in low:
        return "⚠️ Windows denied access to a file or setting. Try running JARVIS as administrator or check folder permissions."
    first = raw.strip().splitlines()[0] if raw.strip() else "Unknown error"
    return "⚠️ Something went wrong: " + first[:220]

def _tidy_tool_result(result: str) -> str:
    """Long raw error dumps from tools become one friendly line."""
    r = str(result)
    if len(r) > 350 and re.search(r"(error|exception|traceback|resource_exhausted)", r[:600], re.I):
        return friendly_error(r)
    return r

def build_conversation_context(session_id, exclude_last_user: bool = True, max_msgs: int = 8, per_msg: int = 450) -> str:
    """Last few chat turns as plain text so follow-ups ('uska price bhi bata', 'usko mail kar do') make sense."""
    try:
        rows = db_load_messages(session_id) if session_id else []
    except Exception:
        return ""
    rows = list(rows)
    if exclude_last_user and rows and rows[-1][0] == "user":
        rows = rows[:-1]
    if rows and rows[0][0] == "jarvis" and "Dashboard Online" in (rows[0][1] or ""):
        rows = rows[1:]                      # the welcome banner isn't conversation
    lines = []
    for role, content, attach in rows[-max_msgs:]:
        txt = re.sub(r"(QR_FILE|SS_FILE|IMG_FILE):\S+", "[image/file]", content or "")
        txt = re.sub(r"\s+", " ", txt).strip()
        if not txt:
            continue
        if len(txt) > per_msg:
            txt = txt[:per_msg] + "…"
        lines.append(f"{'User' if role == 'user' else 'Assistant'}: {txt}")
    return "\n".join(lines)

def run_health_checks(emit):
    """Runs every environment check; emit(label, status, detail, fix) with status ok/warn/fail."""
    import socket, importlib.util
    def check(label, fn):
        try:
            res = fn()
            emit(label, *res)
        except Exception as e:
            emit(label, "fail", str(e)[:160], "")
    def dns(host):
        socket.gethostbyname(host)
    def tcp(host, port=443):
        socket.create_connection((host, port), timeout=5).close()

    def c_python():
        v = sys.version_info
        txt = f"Python {v.major}.{v.minor}.{v.micro}"
        if (v.major, v.minor) >= (3, 13):
            return "warn", txt, "Works, but Python 3.12 is the most compatible version for these packages."
        return "ok", txt, ""
    def c_internet():
        try:
            dns("generativelanguage.googleapis.com"); tcp("generativelanguage.googleapis.com")
        except Exception:
            return "fail", "Cannot reach Google's Gemini servers", "Check Wi-Fi/VPN/antivirus. Try DNS 8.8.8.8 in Windows network settings."
        return "ok", "Google servers reachable", ""
    def c_keys():
        n = len(GEMINI_KEYS)
        if n == 0:
            return "fail", "No Gemini API key saved", "Settings → API Keys → add a key (aistudio.google.com/apikey)."
        try:
            c = genai.Client(api_key=GEMINI_KEYS[0], http_options=types.HttpOptions(timeout=20000))
            r = c.models.generate_content(model="gemini-2.5-flash", contents="Reply with the single word OK")
            return "ok", f"{n} key(s) saved · first key works", ""
        except Exception as e:
            return "fail", f"{n} key(s) saved · first key failed", friendly_error(e).replace("⚠️ ", "")
    def c_tts():
        if not edge_tts:
            return "fail", "edge-tts is not installed", "pip install edge-tts"
        try:
            dns("speech.platform.bing.com"); tcp("speech.platform.bing.com")
        except Exception:
            return "warn", "Neural voice server unreachable (offline Windows voice will be used)", "Fix internet/DNS to get the natural JARVIS/FRIDAY/ELEVEN voices."
        return "ok", f"Neural voices reachable ({persona()['voice_en']})", ""
    def c_audio():
        if not pygame:
            return "fail", "pygame is not installed", "pip install pygame (or pygame-ce)"
        return ("ok", "Audio playback ready", "") if pygame.mixer.get_init() else ("warn", "Audio mixer not initialised", "Check your speakers / default audio device.")
    def c_mic():
        if not sr:
            return "fail", "SpeechRecognition is not installed", "pip install SpeechRecognition"
        try:
            names = sr.Microphone.list_microphone_names()
        except Exception as e:
            return "fail", "Microphone support (PyAudio) is missing", "pip install pyaudio — needed for voice commands and the wake word."
        return ("ok", f"{len(names)} audio input device(s) found", "") if names else ("warn", "No microphone found", "Plug in / enable a microphone in Windows Sound settings.")
    def c_chrome():
        exe = _find_browser_exe()
        if not exe:
            return "warn", "Chrome/Edge not found", "Install Google Chrome to use website automation."
        extra = " · automation window running" if _cdp_alive() else " · starts automatically on first website command"
        return "ok", os.path.basename(exe) + extra, ""
    def c_playwright():
        return ("ok", "Playwright installed", "") if PLAYWRIGHT_AVAILABLE else ("warn", "Playwright not installed", "pip install playwright (browser download NOT needed).")
    def c_google():
        has_c, has_t = os.path.exists(GOOGLE_CREDENTIALS_PATH), os.path.exists(GOOGLE_TOKEN_PATH)
        if not has_c:
            return "warn", "credentials.json not found", "Only needed for Gmail/Drive/Calendar: download it from Google Cloud Console next to this app."
        if not has_t:
            return "warn", "credentials.json found · not connected yet", "Settings → Connect Google Account."
        return "ok", "credentials.json + login token present", ""
    def c_volume():
        if not AudioUtilities:
            return "warn", "pycaw not installed (volume control disabled)", "pip install pycaw comtypes"
        d = AudioUtilities.GetSpeakers()
        vol = d.EndpointVolume if hasattr(d, "EndpointVolume") else None
        return "ok", "Windows volume control available", ""
    def c_optional():
        names = {"uiautomation": "uiautomation", "openpyxl": "openpyxl", "pyautogui": "pyautogui", "psutil": "psutil",
                 "pywhatkit": "pywhatkit", "keyboard": "keyboard", "screen_brightness_control": "screen_brightness_control",
                 "pypdf": "pypdf"}
        missing = [pkg for mod, pkg in names.items() if importlib.util.find_spec(mod) is None]
        return ("ok", "All optional packages present", "") if not missing else ("warn", "Missing: " + ", ".join(missing), "pip install -r requirements.txt")
    def c_storage():
        base = os.path.dirname(os.path.abspath(__file__))
        test = os.path.join(base, ".jv_write_test")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        conn = sqlite3.connect(DB_PATH); conn.execute("SELECT 1"); conn.close()
        return "ok", "App folder writable · memory database OK", ""
    def c_perm():
        return ("ok", "System automation permission is ON", "") if is_system_automation_enabled() else \
               ("warn", "System automation permission is OFF", "Only needed for shell, UI control and the web agent: Settings → Permissions.")

    for label, fn in [("Python", c_python), ("Internet (Google)", c_internet), ("Gemini API key", c_keys),
                      ("Voice (neural TTS)", c_tts), ("Audio playback", c_audio), ("Microphone", c_mic),
                      ("Browser (Chrome)", c_chrome), ("Playwright", c_playwright), ("Google Workspace", c_google),
                      ("Volume control", c_volume), ("Other packages", c_optional), ("Storage", c_storage),
                      ("Automation permission", c_perm)]:
        check(label, fn)

def _persona_prefix(pname: str, head: bool) -> str:
    others = [n for n in PERSONAS if n != pname]
    base = (f"You are {PERSONAS[pname]['full_name']} ({pname}), one of three equally capable AI personas "
            f"({', '.join(PERSONAS)}). You have full power and the complete toolset. "
            "HONESTY RULE: never say you opened, clicked, searched, bought, sent or changed anything unless a tool result "
            "in this conversation confirms it. If you have no tool for something, say plainly that you can't do it yet. "
            "Never invent prices, listings or page contents. "
            "TOOL POLICY: to open a website use open_website; for a simple 'search X on <site>' use web_search; "
            "for multi-step website tasks (forms, cart, menus) use web_agent; to open a PC app or settings page use open_app "
            "or open_deep_link. ")
    if head:
        base += (f"DELEGATION RULES: Handle every request yourself by default. Only when the user gives a genuinely "
                 f"complex, multi-part task (several independent sub-jobs, e.g. research + document + email) may you "
                 f"call delegate_task(assignee, subtask) to hand a self-contained sub-task to {others[0]} or {others[1]}. "
                 f"You are the Head: split the work, delegate, then compile all results into one final answer. "
                 f"If sub-task B needs the result of A, call delegate_task for A first, then pass A's result inside B's subtask text. "
                 f"Never delegate simple tasks or things you can do easily alone.\n\n")
    else:
        base += "You were assigned a sub-task by the Head persona. Complete it fully and return a concise result.\n\n"
    return base

def run_agentic_dispatch(prompt_text: str, active_prompt: str, active_tools: list,
                          status_cb=None, max_steps: int = 4, allow_delegation: bool = True,
                          persona_name: str = None, history: str = "") -> str:
    """Multi-step tool-use loop: if the model calls a tool, the tool's
    result is fed back to it as extra context so it can decide whether to
    call another tool or give a final answer — up to max_steps rounds
    (e.g. "check my calendar, then email the attendees" = 2 tool calls in
    one request). Built on exactly the same generate_content(...)/
    function_calls pattern already proven elsewhere in this file, just
    looped — deliberately avoids newer/less-certain SDK-specific
    multi-turn Content/Part APIs to keep this reliable."""
    global gemini_key_index
    if not GEMINI_KEYS:
        return "⚠️ No Gemini API key configured yet. Add one in Settings → API Keys."

    tool_map = {f.__name__: f for f in ALL_TOOLS}
    tool_map.update(custom_tools_runtime)  # AI-written, user-approved tools from past sessions
    active_tools = list(active_tools) + list(custom_tools_runtime.values())
    pname = persona_name or CURRENT_PERSONA
    if allow_delegation:
        def delegate_task(assignee: str, subtask: str) -> str:
            """Hand a self-contained sub-task of a COMPLEX multi-part request to another persona (assignee must be one of the other two personas by name). Returns that persona's result."""
            a = (assignee or "").strip().upper()
            if a not in PERSONAS or a == pname:
                return f"Invalid assignee '{assignee}'. Choose one of: {', '.join(n for n in PERSONAS if n != pname)}."
            if status_cb:
                status_cb(f"{pname} → delegating to {a}...")
            res = run_agentic_dispatch(subtask, _persona_prefix(a, False) + active_prompt, active_tools,
                                       status_cb=None, max_steps=max_steps, allow_delegation=False, persona_name=a)
            return f"[{a} completed]: {res}"
        tool_map["delegate_task"] = delegate_task
        active_tools = active_tools + [delegate_task]
    keys_snapshot = list(GEMINI_KEYS)
    system_prompt = _persona_prefix(pname, allow_delegation) + active_prompt + build_memory_context()
    if history:
        system_prompt += ("\n\nRECENT CONVERSATION (for context only — resolve words like 'it', 'that', 'him', 'uska', "
                          "'wahi' from it; the user's NEW message is what you must answer now):\n" + history)
    last_exc = None
    context = prompt_text
    all_tool_outputs = []

    for step in range(max_steps):
        step_had_function_call = False
        for attempt in range(len(keys_snapshot)):
            current_key = keys_snapshot[gemini_key_index % len(keys_snapshot)]
            try:
                client_instance = genai.Client(api_key=current_key)
                if status_cb:
                    status_cb(f"{pname} analyzing intent..." if step == 0 else
                               f"{pname} continuing (step {step + 1})...")
                try:
                    res = client_instance.models.generate_content(
                        model="gemini-2.5-flash", contents=context,
                        config=types.GenerateContentConfig(system_instruction=system_prompt, tools=active_tools))
                except Exception:
                    res = client_instance.models.generate_content(
                        model="gemini-2.5-flash", contents=context,
                        config=types.GenerateContentConfig(system_instruction=system_prompt))

                if hasattr(res, "function_calls") and res.function_calls:
                    tool_result_lines = []
                    raw_results = []
                    for fc in res.function_calls:
                        fn_name = fc.name
                        fn_args = dict(fc.args) if hasattr(fc, "args") and fc.args else {}
                        if fn_name in tool_map:
                            if status_cb:
                                status_cb(f"{pname} executing {fn_name}...")
                            try:
                                result = str(tool_map[fn_name](**fn_args))
                            except Exception as t_err:
                                log_error(f"[tool {fn_name}]: {t_err}")
                                result = f"{fn_name} failed: {friendly_error(t_err)}"
                        else:
                            result = f"Unknown tool: {fn_name}"
                        result = _tidy_tool_result(result)
                        tool_result_lines.append(f"{fn_name} result: {result}")
                        all_tool_outputs.append(result)
                        raw_results.append(result)
                    if raw_results and all(r.startswith("DONE:") for r in raw_results):
                        # deterministic tools (web recipes / app launch) already produced the final
                        # user-facing message — return it without another AI call.
                        return "\n".join(r[5:].strip() for r in raw_results)
                    context = (f"Original request: {prompt_text}\n\nTool results so far:\n" +
                               "\n".join(tool_result_lines) +
                               "\n\nIf this fully answers the original request, give the final answer now "
                               "in plain language. Otherwise call another tool to continue.")
                    step_had_function_call = True
                    break  # move to next step with the new context
                elif hasattr(res, "text") and res.text:
                    return res.text
                elif hasattr(res, "candidates") and res.candidates:
                    parts = res.candidates[0].content.parts
                    tp = [p.text for p in parts if hasattr(p, "text") and p.text]
                    if tp:
                        return "\n".join(tp)
                    return "\n\n".join(all_tool_outputs) if all_tool_outputs else ""
                else:
                    return "\n\n".join(all_tool_outputs) if all_tool_outputs else ""
            except Exception as e:
                last_exc = e
                log_error(f"[Agentic dispatch key #{gemini_key_index + 1}]: {e}")
            gemini_key_index = (gemini_key_index + 1) % len(keys_snapshot)
        if not step_had_function_call:
            break  # every key failed this step — stop instead of looping forever

    if all_tool_outputs:
        return "\n\n".join(all_tool_outputs)
    return friendly_error(last_exc) if last_exc else ""

def process_voice_command(text: str, mode_key: str = "Automation", session_id=None) -> str:
    """Headless version of the chat dispatcher — same heuristics as
    ChatView._worker, then the shared multi-step agentic dispatch. Returns
    the reply directly with no UI coupling. Used by the conversation orb."""
    t_low = text.lower()

    if t_low.startswith("@youtube") or "play on youtube" in t_low or ("play" in t_low and "youtube" in t_low):
        q = text.replace("@YouTube", "").replace("@youtube", "").replace("play", "").replace("on youtube", "").strip()
        return play_youtube_video(q if q else "top hits")
    if t_low.startswith("@whatsapp") or "whatsapp" in t_low:
        return open_whatsapp(text)
    if "volume" in t_low or "mute" in t_low or "unmute" in t_low:
        return set_system_volume(text)
    if "brightness" in t_low:
        return set_screen_brightness(text)
    if any(k in t_low for k in ["timer", "remind me", "reminder"]):
        return set_reminder_timer(text)
    if t_low.startswith("@github"):
        q = text.replace("@GitHub", "").replace("@github", "").strip()
        return search_github(q if q else "trending")
    if "generate qr" in t_low or "create qr" in t_low:
        q = text.replace("generate qr for", "").replace("create qr for", "").strip()
        return generate_qr_code(q if q else "https://google.com")
    if t_low.startswith("@gmail") or ("open" in t_low and "gmail" in t_low):
        return open_or_send_gmail()
    if "screenshot" in t_low:
        return take_screenshot()
    if "close" in t_low and ("cmd" in t_low or "terminal" in t_low):
        return close_application("cmd")

    mode_data = MODE_CONFIGS.get(mode_key, MODE_CONFIGS["Automation"])
    reply = run_agentic_dispatch(text, mode_data["system_prompt"], mode_data["tools"],
                                  history=build_conversation_context(session_id, exclude_last_user=False) if session_id else "")
    return reply or "Sorry, I couldn't process that — please check your network connection."

def stop_voice():
    audio_state["is_playing"] = False
    audio_state["task_id"] += 1
    if pygame.mixer.music.get_busy():
        pygame.mixer.music.stop()
    try:
        pygame.mixer.music.unload()
    except Exception:
        pass
    if audio_state["current_btn"]:
        try:
            audio_state["current_btn"].setText("🔊 Listen")
        except Exception:
            pass
        audio_state["current_btn"] = None

def toggle_voice(text: str, btn: QPushButton):
    if audio_state["current_btn"] == btn and audio_state["is_playing"]:
        stop_voice()
        return
    stop_voice()
    audio_state["is_playing"] = True
    audio_state["current_btn"] = btn
    current_task = audio_state["task_id"]
    btn.setText("⏹ Playing...")

    def _worker():
        try:
            clean = prepare_speech_text(text)
            audio_file = os.path.join(tempfile.gettempdir(), f"jarvis_voice_{int(time.time()*1000)}.mp3")
            synthesize_speech(clean, audio_file)
            if not audio_state["is_playing"] or audio_state["task_id"] != current_task:
                if os.path.exists(audio_file):
                    os.remove(audio_file)
                return
            pygame.mixer.music.load(audio_file)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy() and audio_state["is_playing"] and audio_state["task_id"] == current_task:
                pygame.time.Clock().tick(10)
        except Exception as e:
            log_error(f"Voice playback failed (using offline voice): {e}")
            if audio_state["task_id"] == current_task:
                _sapi_speak(text)
        finally:
            if audio_state["task_id"] == current_task:
                stop_voice()
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass
            _safe_remove(audio_file)

    threading.Thread(target=_worker, daemon=True).start()

# ==========================================
# 7. GLASS-PANEL STYLE HELPERS
# ==========================================
def glass_frame(radius=16, bg=None, border="rgba(148, 163, 184, 50)") -> QFrame:
    if bg is None:
        # subtle top-to-bottom gradient + a brighter top edge = real glass
        # "sheen" instead of a flat single-color rectangle.
        bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
              "stop:0 rgba(32, 44, 66, 200), stop:1 rgba(9, 13, 22, 205))")
    f = QFrame()
    f.setObjectName("glassFrame")
    f.setStyleSheet(f"""
        QFrame#glassFrame {{
            background: {bg};
            border: 1px solid {border};
            border-top: 1px solid rgba(210, 226, 255, 60);
            border-radius: {radius}px;
        }}
        QFrame#glassFrame QLabel {{ background: transparent; border: none; }}
    """)
    return f

def accent() -> str:
    return THEMES[current_theme_key]["accent"]

def primary() -> str:
    return THEMES[current_theme_key]["primary"]

def gradient_button(text, height=44, radius=None, parent=None) -> QPushButton:
    radius = radius if radius is not None else height // 2
    btn = QPushButton(text, parent)
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    btn.setFixedHeight(height)
    btn.setStyleSheet(f"""
        QPushButton {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {primary()}, stop:1 {accent()});
            color: white; font-weight: 600; border: none; border-radius: {radius}px; padding: 0 18px;
        }}
        QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {THEMES[current_theme_key]['hover']}, stop:1 {accent()}); }}
        QPushButton:disabled {{ background: #334155; color: #94a3b8; }}
    """)
    return btn

def ghost_button(text, height=34) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    btn.setFixedHeight(height)
    btn.setStyleSheet("""
        QPushButton { background: rgba(17,24,39,190); color: #e2e8f0; border: 1px solid rgba(148,163,184,60);
                      border-radius: 10px; font-size: 11px; font-weight: 600; padding: 0 12px; }
        QPushButton:hover { background: rgba(30,41,59,220); }
    """)
    return btn

def action_tile(icon: str, color: str, label: str, handler, height=52) -> QFrame:
    """A clickable tile with a small colored icon badge + label — used for
    Quick Actions / Apps & Control style one-tap launchers across pages."""
    tile = QFrame()
    tile.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    tile.setFixedHeight(height)
    tile.setStyleSheet("""
        QFrame { background: rgba(17,24,39,190); border: 1px solid rgba(148,163,184,45); border-radius: 10px; }
        QFrame:hover { background: rgba(30,41,59,220); }
    """)
    lay = QHBoxLayout(tile)
    lay.setContentsMargins(10, 6, 10, 6)
    icon_box = QLabel(icon)
    icon_box.setFixedSize(30, 30)
    icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon_box.setStyleSheet(f"background:{color}; border-radius:8px; font-size:14px;")
    lay.addWidget(icon_box)
    text_lbl = QLabel(label)
    text_lbl.setWordWrap(True)
    text_lbl.setStyleSheet("color:#e2e8f0; font-size:11px; font-weight:600; background:transparent;")
    lay.addWidget(text_lbl, 1)
    tile.mousePressEvent = lambda e: handler()
    return tile

def pill_input(icon: str, placeholder: str, masked=False):
    row = glass_frame(radius=22, bg="rgba(17,24,39,200)")
    row.setFixedHeight(46)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(16, 4, 12, 4)
    icon_lbl = QLabel(icon)
    icon_lbl.setStyleSheet("color:#94a3b8; font-size:14px; border:none; background:transparent;")
    lay.addWidget(icon_lbl)
    entry = QLineEdit()
    entry.setPlaceholderText(placeholder)
    entry.setStyleSheet("QLineEdit { border:none; background:transparent; color:#f1f5f9; font-size:12px; }")
    if masked:
        entry.setEchoMode(QLineEdit.EchoMode.Password)
    lay.addWidget(entry, 1)
    return row, entry

def styled_input(placeholder: str, height=36) -> QLineEdit:
    """The consistent 'premium' input-box look — accent-tinted focus glow —
    used everywhere a plain text field is needed (Research, Tools,
    Automation, Tasks, etc.), instead of a flat generic box."""
    entry = QLineEdit()
    entry.setPlaceholderText(placeholder)
    entry.setFixedHeight(height)
    entry.setStyleSheet(f"""
        QLineEdit {{
            background: rgba(17,24,39,210);
            color: #f8fafc;
            border: 1px solid rgba(148,163,184,50);
            border-radius: {height // 2}px;
            padding: 0 14px;
            font-size: 12px;
        }}
        QLineEdit:focus {{ border: 1px solid {accent()}; }}
    """)
    return entry

def styled_output_box(height=140) -> QTextEdit:
    """The consistent 'premium' output/result-box look — a left accent
    stripe + subtle glow border — instead of a flat black box."""
    box = QTextEdit()
    box.setReadOnly(True)
    box.setFixedHeight(height)
    box.setPlainText("Output will appear here...")
    box.setStyleSheet(f"""
        QTextEdit {{
            background: rgba(6,10,18,225);
            color: #a5f3fc;
            font-family: Consolas;
            font-size: 11px;
            border: 1px solid rgba(148,163,184,45);
            border-left: 3px solid {accent()};
            border-radius: 10px;
            padding: 8px;
        }}
    """)
    return box

# ==========================================
# 7b. GENERIC THREAD-SAFE BACKGROUND RUNNER
# ==========================================
class _ResultSignal(QObject):
    done = pyqtSignal(str)

def run_bg(owner: QObject, fn, callback):
    """Runs fn() on a background thread and delivers its return value to
    callback on the GUI thread. The signal is parented to `owner` so it stays
    alive exactly as long as the widget that needs the result does."""
    sig = _ResultSignal(owner)
    sig.done.connect(callback)

    def _t():
        try:
            res = fn()
        except Exception as e:
            res = f"Error: {e}"
        sig.done.emit(str(res))

    threading.Thread(target=_t, daemon=True).start()

def result_box(height=110) -> QTextEdit:
    return styled_output_box(height=height)

def set_result_box(box: QTextEdit, text: str):
    box.setPlainText(text)

def add_image_preview(layout, img_path: str, caption: str):
    """Shared inline-image renderer used by Chat bubbles and the quick-action
    pages (Apps & Control) for QR codes / screenshots."""
    cap_lbl = QLabel(caption)
    cap_lbl.setStyleSheet("color:#e2e8f0; font-size:12px; background:transparent;")
    layout.addWidget(cap_lbl)
    try:
        if os.path.exists(img_path):
            pix = QPixmap(img_path)
            if not pix.isNull():
                pix = pix.scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                img_lbl = QLabel()
                img_lbl.setPixmap(pix)
                img_lbl.setStyleSheet("background:transparent;")
                layout.addWidget(img_lbl)
                path_lbl = QLabel(f"Saved to: {img_path}")
                path_lbl.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
                layout.addWidget(path_lbl)
                return
    except Exception:
        pass
    err_lbl = QLabel("(couldn't preview image)")
    err_lbl.setStyleSheet("color:#64748b; font-size:11px; background:transparent;")
    layout.addWidget(err_lbl)

# ==========================================
# 8. REACTOR WIDGET (real QGraphicsBlurEffect glow)
# ==========================================
class _ReactorRings(QWidget):
    """Draws the reactor in one of three persona shapes — 'rings' (JARVIS),
    'hexagon' (FRIDAY), 'orbital' (ELEVEN) — as a sibling of the glow label
    so stacking order between the two is controllable (fixes the
    glow-covers-rings bug a plain paintEvent-on-parent + child-QLabel combo
    would otherwise have, since child widgets always paint over their
    parent's own paintEvent)."""
    def __init__(self, size, parent=None):
        super().__init__(parent)
        self._size = size
        self._angle = 0
        self.shape = persona()["reactor_shape"]
        self.label = CURRENT_PERSONA
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

    def set_angle(self, angle):
        self._angle = angle
        self.update()

    def set_persona(self, shape, label):
        self.shape = shape
        self.label = label
        self.update()

    @staticmethod
    def _poly(cx, cy, r, sides, rot_deg=0):
        import math as _m
        pts = []
        for i in range(sides):
            ang = _m.radians(rot_deg + i * 360.0 / sides)
            pts.append(QPointF(cx + r * _m.cos(ang), cy + r * _m.sin(ang)))
        return QPolygonF(pts)

    def _paint_glow_field(self, p, c, col, radius_frac=0.46):
        import math
        pulse = 0.5 + 0.5 * math.sin(math.radians(getattr(self, "_angle", 0) * 2.0))   # 0..1 breathing
        grad = QRadialGradient(c, c, self._size * (radius_frac + 0.03 * pulse))
        core = QColor(col); core.setAlpha(int(18 + 22 * pulse))
        glow_col = QColor(col); glow_col.setAlpha(int(70 + 50 * pulse))
        edge_col = QColor(col); edge_col.setAlpha(0)
        grad.setColorAt(0.0, core)
        grad.setColorAt(0.55, edge_col)
        grad.setColorAt(0.80, glow_col)
        grad.setColorAt(1.0, edge_col)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        r = int(self._size * radius_frac)
        p.drawEllipse(QPoint(int(c), int(c)), r, r)

    def _paint_label(self, p, color, size_frac=0.055):
        p.setPen(color)
        f = QFont("Consolas")
        f.setBold(True)
        f.setPixelSize(max(6, int(self._size * size_frac)))
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.label)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self._size / 2
        col = QColor(accent())
        if self.shape == "hexagon":
            self._paint_hexagon(p, c, col)
        elif self.shape == "orbital":
            self._paint_orbital(p, c, col)
        else:
            self._paint_rings(p, c, col)

    # ---- JARVIS: concentric rings + orbiting particles ----
    def _paint_rings(self, p, c, col):
        import math as _m
        s = self._size
        self._paint_glow_field(p, c, col, 0.46)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, 1.6))
        p.drawEllipse(QPoint(int(c), int(c)), int(s * 0.46), int(s * 0.46))
        p.setPen(QPen(col, 1.2))
        p.drawEllipse(QPoint(int(c), int(c)), int(s * 0.34), int(s * 0.34))

        p.save()
        p.translate(c, c)
        p.rotate(self._angle)
        particle_defs = [(0, 0.52, 3.4), (55, 0.58, 2.2), (120, 0.50, 2.8),
                          (170, 0.60, 2.0), (230, 0.54, 3.0), (300, 0.59, 2.4)]
        for base_angle, radius_frac, dot_r in particle_defs:
            rad = _m.radians(base_angle)
            x = s * radius_frac * _m.cos(rad)
            y = s * radius_frac * _m.sin(rad)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(col))
            p.drawEllipse(int(x - dot_r), int(y - dot_r), int(dot_r * 2), int(dot_r * 2))
        p.restore()

        core_grad = QRadialGradient(c, c, s * 0.20)
        core_edge = QColor(col)
        core_edge.setAlpha(90)
        core_grad.setColorAt(0.0, core_edge)
        core_grad.setColorAt(1.0, QColor(9, 14, 23, 235))
        p.setPen(QPen(col, 1.6))
        p.setBrush(QBrush(core_grad))
        p.drawEllipse(QPoint(int(c), int(c)), int(s * 0.20), int(s * 0.20))
        self._paint_label(p, QColor(accent()), 0.055)

    # ---- FRIDAY: hexagonal nanotech with six inward blades ----
    def _paint_hexagon(self, p, c, col):
        s = self._size
        self._paint_glow_field(p, c, col, 0.48)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, 2.0))
        p.drawPolygon(self._poly(c, c, s * 0.46, 6, -90))
        p.setPen(QPen(col, 1.0))
        p.drawPolygon(self._poly(c, c, s * 0.37, 6, -90))

        p.save()
        p.translate(c, c)
        p.rotate(self._angle * 0.25)
        blade_fill = QColor(col)
        blade_fill.setAlpha(110)
        for i in range(6):
            p.save()
            p.rotate(i * 60)
            tri = QPolygonF([QPointF(-0.085 * s, -0.33 * s), QPointF(0.085 * s, -0.33 * s),
                              QPointF(0, -0.16 * s)])
            p.setBrush(QBrush(blade_fill))
            p.setPen(QPen(col, 1.2))
            p.drawPolygon(tri)
            p.setPen(QPen(QColor("#fbbf24"), 1.2))
            p.drawLine(QPointF(0, -0.33 * s), QPointF(0, -0.18 * s))
            p.restore()
        p.restore()

        p.setPen(QPen(QColor(255, 255, 255), 1.6))
        p.setBrush(QBrush(QColor(255, 232, 237, 240)))
        p.drawPolygon(self._poly(c, c, s * 0.15, 6, -90))
        self._paint_label(p, QColor(accent()).darker(150), 0.05)

    # ---- ELEVEN: orbital rings around a crystal-octagon core ----
    def _paint_orbital(self, p, c, col):
        s = self._size
        self._paint_glow_field(p, c, col, 0.47)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, 1.8))
        p.drawEllipse(QRectF(c - s * 0.47, c - s * 0.40, s * 0.94, s * 0.80))

        amber = QColor("#fbbf24")
        p.save()
        p.translate(c, c)
        p.rotate(self._angle * 0.6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, 2.0))
        p.drawEllipse(QRectF(-s * 0.40, -s * 0.14, s * 0.80, s * 0.28))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(amber))
        p.drawEllipse(QPointF(s * 0.40, 0), 3.0, 3.0)
        p.restore()

        p.save()
        p.translate(c, c)
        p.rotate(-self._angle * 0.4 + 60)
        dashed = QPen(col, 1.6)
        dashed.setStyle(Qt.PenStyle.DashLine)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(dashed)
        p.drawEllipse(QRectF(-s * 0.38, -s * 0.12, s * 0.76, s * 0.24))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(amber))
        p.drawEllipse(QPointF(-s * 0.38, 0), 3.0, 3.0)
        p.restore()

        body_fill = QColor(col)
        body_fill.setAlpha(120)
        p.setBrush(QBrush(body_fill))
        p.setPen(QPen(col, 1.4))
        p.drawPolygon(self._poly(c, c, s * 0.19, 8, -22.5))
        p.setBrush(QBrush(QColor(243, 232, 255, 242)))
        p.setPen(QPen(QColor(255, 255, 255), 1.6))
        p.drawPolygon(self._poly(c, c, s * 0.125, 8, -22.5))
        self._paint_label(p, QColor(accent()).darker(160), 0.05)

class ReactorWidget(QWidget):
    def __init__(self, size=200, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._angle = 0
        self.setStyleSheet("background: transparent;")

        # glow is painted softly inside _ReactorRings (no blur effect: avoids the solid-blob/black-patch rendering bug)

        # rings drawn on a sibling widget, explicitly raised above the glow
        self.rings = _ReactorRings(size, self)
        self.rings.setGeometry(0, 0, size, size)
        self.rings.raise_()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60)

    def _tick(self):
        self._angle = (self._angle + 4) % 360
        self.rings.set_angle(self._angle)

    def refresh_theme(self):
        self.rings.update()

    def apply_persona(self):
        """Re-reads the active persona's shape + label + color and repaints."""
        self.rings.set_persona(persona()["reactor_shape"], CURRENT_PERSONA)
        self.refresh_theme()

# ==========================================
# 9. SPARKLE "thinking" INDICATOR
# ==========================================
class SparkleIndicator(QWidget):
    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        self.icon_lbl = QLabel("✨")
        self.status_lbl = QLabel("J.A.R.V.I.S. analyzing intent...")
        self.status_lbl.setStyleSheet("color:#38bdf8; font-family:Consolas; font-weight:bold; font-size:11px; background:transparent;")
        self.icon_lbl.setStyleSheet("font-size:14px; background:transparent;")
        lay.addWidget(self.icon_lbl)
        lay.addWidget(self.status_lbl)
        lay.addStretch()
        self._frames = ["✨", "✦", "✧", "⋆"]
        self._idx = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(250)

    def _animate(self):
        self._idx = (self._idx + 1) % len(self._frames)
        self.icon_lbl.setText(self._frames[self._idx])

    def set_status(self, text):
        self.status_lbl.setText(text)

    def stop(self):
        self._timer.stop()

# ==========================================
# 10. CODE CARD (with in-place "Run Script")
# ==========================================
class CodeRunSignal(QObject):
    finished = pyqtSignal(str)

class CodeCard(QFrame):
    def __init__(self, lang: str, code: str):
        super().__init__()
        self.code = code
        self.setStyleSheet("QFrame { background:#050811; border:1px solid #1e293b; border-radius:8px; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background:#0d1424; border-top-left-radius:8px; border-top-right-radius:8px;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 8, 4)
        lang_lbl = QLabel(f"⚡ {lang}")
        lang_lbl.setStyleSheet("color:#38bdf8; font-family:Consolas; font-size:10px; font-weight:bold; background:transparent;")
        hl.addWidget(lang_lbl)
        hl.addStretch()
        copy_btn = ghost_button("📋 Copy", height=22)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(code))
        run_btn = ghost_button("▶ Run", height=22)
        run_btn.clicked.connect(lambda: self._run(run_btn))
        hl.addWidget(copy_btn)
        hl.addWidget(run_btn)
        outer.addWidget(header)

        self.code_box = QTextEdit()
        self.code_box.setPlainText(code)
        self.code_box.setReadOnly(True)
        lines = min(max(code.count("\n") + 1, 3), 14)
        self.code_box.setFixedHeight(lines * 18 + 12)
        self.code_box.setStyleSheet("QTextEdit { background:#050811; color:#a5f3fc; font-family:Consolas; "
                                     "font-size:11px; border:none; }")
        outer.addWidget(self.code_box)

        self.console = None
        self._sig = CodeRunSignal()
        self._sig.finished.connect(self._show_output)

    def _run(self, btn):
        btn.setText("⏳ Running...")
        btn.setEnabled(False)

        def _exec():
            try:
                proc = subprocess.run([sys.executable, "-c", self.code], capture_output=True, text=True, timeout=12)
                out = proc.stdout
                err = proc.stderr
                res = (out or "") + (f"STDERR:\n{err}" if err else "")
                if not res:
                    res = "Process executed successfully (Exit code: 0)"
            except subprocess.TimeoutExpired:
                res = "Execution Error: Script timed out (12s limit)."
            except Exception as e:
                res = f"Execution Error: {e}"
            self._sig.finished.emit(res)
            QTimer.singleShot(0, lambda: (btn.setText("▶ Run"), btn.setEnabled(True)))

        threading.Thread(target=_exec, daemon=True).start()

    def _show_output(self, res_str):
        if not self.console:
            self.console = QTextEdit()
            self.console.setReadOnly(True)
            self.console.setFixedHeight(70)
            self.console.setStyleSheet("QTextEdit { background:#030712; color:#34d399; font-family:Consolas; "
                                        "font-size:10px; border-top:1px solid #10b981; }")
            self.layout().addWidget(self.console)
        self.console.setPlainText(res_str.strip())

# ==========================================
# 11. CHAT BUBBLES
# ==========================================
class UserBubble(QWidget):
    def __init__(self, text: str, file_name: str = ""):
        super().__init__()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.addStretch()
        bubble = QFrame()
        bubble.setMaximumWidth(420)
        bubble.setStyleSheet("QFrame { background:#1e293b; border-radius:12px; }")
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(12, 8, 12, 8)
        sender = QLabel("You")
        sender.setStyleSheet("color:#38bdf8; font-weight:bold; font-size:10px; background:transparent;")
        bl.addWidget(sender)
        if file_name:
            tag = QLabel(f"📎 {file_name}")
            tag.setStyleSheet("color:#f59e0b; font-weight:bold; font-size:10px; background:transparent;")
            bl.addWidget(tag)
        msg = QLabel(text if text else "[File Attached]")
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#f8fafc; font-size:12px; background:transparent;")
        bl.addWidget(msg)
        outer.addWidget(bubble)

class JarvisBubble(QWidget):
    def __init__(self, text: str = ""):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 60, 6)
        badge = QLabel(f"⚡ {persona()['full_name']}")
        badge.setStyleSheet(f"color:{accent()}; font-family:Consolas; font-weight:bold; font-size:11px; background:transparent;")
        outer.addWidget(badge)

        if text.startswith("QR_FILE:"):
            self._add_image_preview(outer, text.split("QR_FILE:", 1)[1].strip(), "QR Code generated:")
        elif text.startswith("SS_FILE:"):
            self._add_image_preview(outer, text.split("SS_FILE:", 1)[1].strip(), "Screenshot captured:")
        elif text.startswith("IMG_FILE:"):
            self._add_image_preview(outer, text.split("IMG_FILE:", 1)[1].strip(), "Generated image:")
        else:
            for block in parse_content_blocks(text):
                if block["type"] == "text":
                    lbl = QLabel(block["content"])
                    lbl.setWordWrap(True)
                    lbl.setStyleSheet("color:#e2e8f0; font-size:12px; background:transparent;")
                    outer.addWidget(lbl)
                else:
                    outer.addWidget(CodeCard(block["lang"], block["content"]))

        actions = QHBoxLayout()
        copy_btn = ghost_button("📋 Copy")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(clean_display_text(text)))
        speak_btn = ghost_button("🔊 Listen")
        speak_btn.clicked.connect(lambda: toggle_voice(text, speak_btn))
        actions.addWidget(copy_btn)
        actions.addWidget(speak_btn)
        actions.addStretch()
        outer.addLayout(actions)

    def _add_image_preview(self, outer, img_path, caption):
        add_image_preview(outer, img_path, caption)

# ==========================================
# 12. TITLE BAR (draggable, minimize, close)
# ==========================================
class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__()
        self._win = window
        self._drag_pos = None
        self.setFixedHeight(42)
        self.setStyleSheet("background: rgba(8,11,19,230); border-top-left-radius:16px; border-top-right-radius:16px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 10, 0)

        logo = QLabel(f"{persona()['icon']}  {persona()['full_name'].rstrip('.')}")
        logo.setStyleSheet(f"color:{accent()}; font-family:Consolas; font-weight:bold; font-size:13px; background:transparent;")
        lay.addWidget(logo)
        self.logo_lbl = logo

        self.online_lbl = QLabel("🟢 System Online")
        self.online_lbl.setStyleSheet("color:#10b981; font-size:11px; font-weight:bold; background:transparent; margin-left:16px;")
        lay.addWidget(self.online_lbl)
        lay.addStretch()

        self.clock_lbl = QLabel("")
        self.clock_lbl.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        lay.addWidget(self.clock_lbl)

        min_btn = QPushButton("–")
        min_btn.setFixedSize(28, 28)
        min_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        min_btn.setStyleSheet("QPushButton{background:transparent;color:#94a3b8;font-weight:bold;border:none;font-size:15px;}"
                               "QPushButton:hover{background:#1e293b;border-radius:6px;}")
        min_btn.clicked.connect(self._win.showMinimized)
        lay.addWidget(min_btn)

        self.max_btn = QPushButton("▢")
        self.max_btn.setFixedSize(28, 28)
        self.max_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.max_btn.setStyleSheet("QPushButton{background:transparent;color:#94a3b8;font-weight:bold;border:none;font-size:12px;}"
                                    "QPushButton:hover{background:#1e293b;border-radius:6px;}")
        self.max_btn.clicked.connect(self._toggle_maximize)
        lay.addWidget(self.max_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setStyleSheet("QPushButton{background:transparent;color:#94a3b8;font-weight:bold;border:none;font-size:12px;}"
                                 "QPushButton:hover{background:#7f1d1d;color:white;border-radius:6px;}")
        close_btn.clicked.connect(lambda: self._win.close_to_tray())
        lay.addWidget(close_btn)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_clock)
        self._timer.start(30000)
        self._tick_clock()

    def _tick_clock(self):
        self.clock_lbl.setText(datetime.now().strftime("%a, %b %d, %Y  |  %I:%M %p"))

    def _toggle_maximize(self):
        if self._win.isMaximized():
            self._win.showNormal()
            self.max_btn.setText("▢")
        else:
            self._win.showMaximized()
            self.max_btn.setText("❐")

    def apply_persona(self):
        self.logo_lbl.setText(f"{persona()['icon']}  {persona()['full_name'].rstrip('.')}")

    def refresh_theme(self):
        self.logo_lbl.setStyleSheet(f"color:{accent()}; font-family:Consolas; font-weight:bold; font-size:13px; background:transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()

# ==========================================
# 12b. TOAST NOTIFICATION (floating, auto-dismiss)
# ==========================================
class Toast(QWidget):
    """A small floating notification that appears over the bottom-right of
    the given parent window, fades in, holds, fades out, then deletes
    itself. This is what makes background-thread errors/success messages
    actually visible to the user (console print() alone is not, once this
    is packaged as a windowed .exe)."""
    KIND_COLORS = {"info": "#38bdf8", "success": "#10b981", "error": "#ef4444"}
    KIND_ICONS = {"info": "ℹ️", "success": "✅", "error": "⚠️"}

    def __init__(self, parent_window, message: str, kind: str = "info"):
        super().__init__(parent_window)
        self._win = parent_window
        color = self.KIND_COLORS.get(kind, "#38bdf8")
        icon = self.KIND_ICONS.get(kind, "ℹ️")

        # content lives in an inner frame (which owns the drop-shadow);
        # the opacity-fade effect goes on this outer widget instead, since
        # Qt only allows one graphics effect per widget.
        outer_lay = QVBoxLayout(self)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{ background: rgba(10,15,25,235); border: 1px solid {color};
                       border-left: 4px solid {color}; border-radius: 10px; }}
        """)
        outer_lay.addWidget(card)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 10, 14, 10)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("background:transparent; font-size:13px;")
        lay.addWidget(icon_lbl)
        text_lbl = QLabel(message[:140])
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet("background:transparent; color:#f1f5f9; font-size:11px;")
        lay.addWidget(text_lbl, 1)

        self.setFixedWidth(320)
        self.adjustSize()
        self._reposition()

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0.0)
        self.show()
        self.raise_()

        self._fade_in = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_in.setDuration(220)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.start()

        QTimer.singleShot(3200, self._fade_out)

    def _reposition(self):
        if self._win:
            x = self._win.width() - self.width() - 24
            y = self._win.height() - self.height() - 24
            self.move(x, y)

    def _fade_out(self):
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(300)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self.deleteLater)
        anim.start()
        self._fade_out_anim = anim

# ==========================================
# 13. SIDEBAR NAV
# ==========================================
NAV_ITEMS = [
    ("🏠", "Home", "home"), ("💬", "Chat", "chat"), ("🎙️", "Voice", "voice"),
    ("🗒️", "Tasks", "tasks"), ("⚙️", "Automation", "automation"), ("🧩", "Apps & Control", "apps"),
    ("📁", "Files & Memory", "files"), ("🔎", "Research", "research"), ("🛠️", "Tools", "tools"),
    ("⚙️", "Settings", "settings"),
]

class Sidebar(QWidget):
    def __init__(self, on_select):
        super().__init__()
        self.on_select = on_select
        self.setFixedWidth(240)
        self.setStyleSheet("background: rgba(8,11,19,230); border-right:1px solid rgba(148,163,184,30);")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 20, 14, 14)

        title_row = QHBoxLayout()
        dot = QLabel(persona()["icon"])
        dot.setStyleSheet(f"color:{accent()}; font-size:20px; background:transparent;")
        title_row.addWidget(dot)
        self.dot = dot
        title_col = QVBoxLayout()
        name_lbl = QLabel(persona()["full_name"])
        name_lbl.setStyleSheet("color:#f1f5f9; font-family:Consolas; font-weight:bold; font-size:15px; background:transparent;")
        sub_lbl = QLabel(persona()["tagline"])
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet("color:#475569; font-family:Consolas; font-size:7px; background:transparent;")
        self.name_lbl = name_lbl
        self.sub_lbl = sub_lbl
        title_col.addWidget(name_lbl)
        title_col.addWidget(sub_lbl)
        title_row.addLayout(title_col)
        title_row.addStretch()
        lay.addLayout(title_row)
        lay.addSpacing(20)

        self.buttons = {}
        for icon, label, key in NAV_ITEMS:
            safe_label = label.replace("&", "&&")  # Qt treats a lone "&" in QPushButton
                                                     # text as a keyboard-mnemonic marker and
                                                     # swallows it — "&&" displays a literal "&"
            b = QPushButton(f"  {icon}   {safe_label}")
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setFixedHeight(36)
            b.clicked.connect(lambda _, k=key: self.on_select(k))
            lay.addWidget(b)
            self.buttons[key] = b
        lay.addStretch()

        self.profile_card = glass_frame(radius=10, bg="rgba(11,16,27,200)")
        pc_lay = QVBoxLayout(self.profile_card)
        self.profile_name_lbl = QLabel(f"Hello, {USER_NAME}")
        self.profile_name_lbl.setStyleSheet("color:#f1f5f9; font-weight:bold; font-size:12px; background:transparent;")
        sub = QLabel("Let's build something amazing today.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#64748b; font-size:10px; background:transparent;")
        pc_lay.addWidget(self.profile_name_lbl)
        pc_lay.addWidget(sub)
        lay.addWidget(self.profile_card)

        self.set_active("home")
        self._current_key = "home"

    def set_active(self, key):
        for k, b in self.buttons.items():
            active = (k == key)
            if active:
                b.setStyleSheet(f"""
                    QPushButton {{ text-align:left; border:none; border-radius:10px; font-size:12px;
                        font-weight:bold; color:#ffffff;
                        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {primary()}, stop:1 {accent()}); }}
                """)
            else:
                b.setStyleSheet("""
                    QPushButton { text-align:left; border:none; border-radius:10px; font-size:12px;
                        font-weight:normal; color:#94a3b8; background:transparent; }
                    QPushButton:hover { background: rgba(17,24,39,200); color:#e2e8f0; }
                """)

    def refresh_theme(self):
        self.dot.setStyleSheet(f"color:{accent()}; font-size:20px; background:transparent;")
        self.set_active(self._current_key if hasattr(self, "_current_key") else "home")

    def apply_persona(self):
        self.dot.setText(persona()["icon"])
        self.name_lbl.setText(persona()["full_name"])
        self.sub_lbl.setText(persona()["tagline"])

    def update_name(self):
        self.profile_name_lbl.setText(f"Hello, {USER_NAME}")


# ==========================================
# 14. HOME VIEW
# ==========================================
class PersonaCapsules(QWidget):
    """Three capsule buttons — one per persona. Each shows its own persona
    color even while inactive; the active one is filled with its gradient.
    Clicking switches the whole app (colors, reactor shape, name, voice)."""
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.buttons = {}
        for name, p in PERSONAS.items():
            b = QPushButton(f"{p['icon']}  {p['full_name'].rstrip('.')}")
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setFixedHeight(34)
            b.clicked.connect(lambda _, n=name: self.mw.set_persona(n))
            lay.addWidget(b)
            self.buttons[name] = b
        self.refresh()

    def refresh(self):
        for name, b in self.buttons.items():
            c = THEMES[PERSONAS[name]["theme_key"]]
            if name == CURRENT_PERSONA:
                b.setStyleSheet(f"""
                    QPushButton {{ border-radius:17px; font-size:11px; font-weight:bold; padding:0 16px;
                        color:#ffffff; border:1px solid {c['accent']};
                        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {c['primary']}, stop:1 {c['accent']}); }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ border-radius:17px; font-size:11px; font-weight:600; padding:0 16px;
                        color:{c['accent']}; border:1px solid {c['accent']}; background: rgba(10,15,25,170); }}
                    QPushButton:hover {{ background: rgba(30,41,59,220); }}
                """)

class HomeView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        # ---------- center column ----------
        center = QVBoxLayout()
        center.addStretch(1)

        self.greeting_lbl = QLabel("")
        self.greeting_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.greeting_lbl.setStyleSheet("color:#f8fafc; font-size:26px; font-weight:bold; background:transparent;")
        center.addWidget(self.greeting_lbl)

        sub = QLabel("How can I assist you today?")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color:{accent()}; font-size:13px; background:transparent;")
        center.addWidget(sub)
        self.sub_lbl = sub

        capsule_row = QHBoxLayout()
        capsule_row.addStretch()
        self.persona_capsules = PersonaCapsules(main_window)
        capsule_row.addWidget(self.persona_capsules)
        capsule_row.addStretch()
        center.addSpacing(6)
        center.addLayout(capsule_row)

        reactor_row = QHBoxLayout()
        reactor_row.addStretch()
        self.reactor = ReactorWidget(260)
        reactor_row.addWidget(self.reactor)
        reactor_row.addStretch()
        center.addLayout(reactor_row)

        ask_row = glass_frame(radius=24, bg="rgba(10,15,25,215)", border=f"{accent()}")
        ask_row.setFixedHeight(56)
        self.ask_row = ask_row
        ask_lay = QHBoxLayout(ask_row)
        ask_lay.setContentsMargins(20, 4, 8, 4)
        self.ask_entry = QLineEdit()
        self.ask_entry.setPlaceholderText("Ask me anything...")
        self.ask_entry.setStyleSheet("QLineEdit{border:none;background:transparent;color:#f8fafc;font-size:13px;}")
        self.ask_entry.returnPressed.connect(self._home_send)
        ask_lay.addWidget(self.ask_entry, 1)
        send_btn = gradient_button("➤", height=42, radius=21)
        send_btn.setFixedWidth(50)
        send_btn.clicked.connect(self._home_send)
        ask_lay.addWidget(send_btn)
        center.addWidget(ask_row)

        chip_row = QHBoxLayout()
        chip_row.addStretch()
        self.chip_btns = {}
        for mode_key in ["General", "Research", "Coder", "Creative", "Automation"]:
            label = mode_key
            chip_text = MODE_CONFIGS[mode_key]["badge"]
            cb = QPushButton(chip_text)
            cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            cb.setFixedHeight(30)
            cb.clicked.connect(lambda _, l=label, m=mode_key: self._pick_chip(l, m))
            chip_row.addWidget(cb)
            self.chip_btns[label] = cb
        chip_row.addStretch()
        center.addLayout(chip_row)
        self._pick_chip("General", "General")

        hint = QLabel('Try: "Open VS Code"  |  "Summarize this PDF"  |  "Control my system"  |  "Research latest AI news"')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color:#64748b; font-size:10px; background:transparent;")
        center.addWidget(hint)

        tagline1 = QLabel("MORE THAN AN ASSISTANT")
        tagline1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline1.setStyleSheet("color:#475569; font-family:Consolas; font-size:9px; background:transparent; margin-top:18px;")
        tagline2 = QLabel("A PARTNER IN PROGRESS")
        tagline2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline2.setStyleSheet(f"color:{accent()}; font-family:Consolas; font-size:15px; font-weight:bold; background:transparent;")
        self.tagline2 = tagline2
        center.addWidget(tagline1)
        center.addWidget(tagline2)
        center.addStretch(2)

        root.addLayout(center, 3)

        # ---------- right column ----------
        right = QVBoxLayout()
        right.setSpacing(12)

        quote_card = glass_frame()
        qc_lay = QVBoxLayout(quote_card)
        self.quote_lbl = QLabel("")
        self.quote_lbl.setWordWrap(True)
        self.quote_lbl.setStyleSheet("color:#cbd5e1; font-size:12px; font-style:italic; background:transparent;")
        qc_lay.addWidget(self.quote_lbl)
        qc_sign = QLabel("— J.A.R.V.I.S")
        qc_sign.setStyleSheet(f"color:{accent()}; font-weight:bold; font-size:10px; background:transparent;")
        qc_lay.addWidget(qc_sign)
        right.addWidget(quote_card)

        status_card = glass_frame()
        sc_lay = QVBoxLayout(status_card)
        sc_head = QHBoxLayout()
        sc_title = QLabel("System Status")
        sc_title.setStyleSheet("color:#f1f5f9; font-weight:bold; font-size:12px; background:transparent;")
        sc_live = QLabel("● Live")
        sc_live.setStyleSheet("color:#10b981; font-weight:bold; font-size:10px; background:transparent;")
        sc_head.addWidget(sc_title)
        sc_head.addStretch()
        sc_head.addWidget(sc_live)
        sc_lay.addLayout(sc_head)

        self.status_bars = {}
        for label in ["CPU", "RAM", "Disk"]:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(40)
            lbl.setStyleSheet("color:#94a3b8; font-size:10px; background:transparent;")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            bar.setStyleSheet(f"""
                QProgressBar {{ background:#111827; border-radius:4px; }}
                QProgressBar::chunk {{ background:{accent()}; border-radius:4px; }}
            """)
            val_lbl = QLabel("—")
            val_lbl.setFixedWidth(38)
            val_lbl.setStyleSheet("color:#94a3b8; font-size:10px; background:transparent;")
            row.addWidget(lbl)
            row.addWidget(bar, 1)
            row.addWidget(val_lbl)
            sc_lay.addLayout(row)
            self.status_bars[label] = (bar, val_lbl)

        battery_row = QHBoxLayout()
        battery_lbl = QLabel("🔋 Battery")
        battery_lbl.setStyleSheet("color:#94a3b8; font-size:10px; background:transparent;")
        self.battery_val_lbl = QLabel("N/A")
        self.battery_val_lbl.setStyleSheet("color:#f1f5f9; font-size:10px; font-weight:bold; background:transparent;")
        battery_row.addWidget(battery_lbl)
        battery_row.addStretch()
        battery_row.addWidget(self.battery_val_lbl)
        sc_lay.addLayout(battery_row)

        network_row = QHBoxLayout()
        network_lbl = QLabel("📶 Network")
        network_lbl.setStyleSheet("color:#94a3b8; font-size:10px; background:transparent;")
        self.network_val_lbl = QLabel("—")
        self.network_val_lbl.setStyleSheet("color:#f1f5f9; font-size:10px; font-weight:bold; background:transparent;")
        network_row.addWidget(network_lbl)
        network_row.addStretch()
        network_row.addWidget(self.network_val_lbl)
        sc_lay.addLayout(network_row)
        self._net_prev = {"bytes": None, "t": None}

        ai_row = QHBoxLayout()
        ai_lbl = QLabel("AI Core")
        ai_lbl.setStyleSheet("color:#94a3b8; font-size:10px; background:transparent;")
        ai_val = QLabel("Online")
        ai_val.setStyleSheet("color:#10b981; font-weight:bold; font-size:10px; background:transparent;")
        ai_row.addWidget(ai_lbl)
        ai_row.addStretch()
        ai_row.addWidget(ai_val)
        sc_lay.addLayout(ai_row)
        right.addWidget(status_card)

        quick_card = glass_frame()
        qk_lay = QVBoxLayout(quick_card)
        qk_title = QLabel("Quick Actions")
        qk_title.setStyleSheet("color:#f1f5f9; font-weight:bold; font-size:12px; background:transparent;")
        qk_lay.addWidget(qk_title)
        qk_grid = QGridLayout()
        qk_grid.setSpacing(4)
        actions = [
            ("📸", "#f59e0b", "Screenshot", lambda: self._quick("Take a screenshot of my desktop")),
            ("🖥️", "#38bdf8", "System", lambda: self._quick("What's my current system status?")),
            ("📎", "#a855f7", "Attach", lambda: self.mw.chat_view.attach_file()),
            ("</>", "#10b981", "Run Code", lambda: self._quick("Write a small python script to ")),
            ("🖼️", "#ec4899", "Generate Image", self._generate_image),
        ]
        for i, (icon, color, label, cmd) in enumerate(actions):
            b = action_tile(icon, color, label, cmd, height=48)
            qk_grid.addWidget(b, i // 2, i % 2)
        qk_lay.addLayout(qk_grid)
        right.addWidget(quick_card)

        activity_card = glass_frame()
        ac_lay = QVBoxLayout(activity_card)
        ac_title = QLabel("Recent Activity")
        ac_title.setStyleSheet("color:#f1f5f9; font-weight:bold; font-size:12px; background:transparent;")
        ac_lay.addWidget(ac_title)
        self.activity_list_lay = QVBoxLayout()
        ac_lay.addLayout(self.activity_list_lay)
        right.addWidget(activity_card, 1)

        right_wrap = QWidget()
        right_wrap.setLayout(right)
        right_wrap.setFixedWidth(300)
        root.addWidget(right_wrap)

    def _pick_chip(self, label, mode_key):
        global active_mode_key
        active_mode_key = mode_key
        self._active_chip_label = label
        self.mw.chat_view.set_mode_badge(mode_key)
        for l, b in self.chip_btns.items():
            active = (l == label)
            b.setStyleSheet(f"""
                QPushButton {{ border-radius:14px; font-size:11px; font-weight:bold; padding:0 14px;
                    color:{'white' if active else '#94a3b8'};
                    background:{primary() if active else 'rgba(17,24,39,200)'}; border:none; }}
                QPushButton:hover {{ background:{accent() if active else 'rgba(30,41,59,220)'}; }}
            """)

    def _home_send(self):
        text = self.ask_entry.text().strip()
        if not text:
            return
        self.ask_entry.clear()
        self.mw.select_view("chat")
        self.mw.chat_view.send_text(text)

    def _quick(self, prompt_text):
        self.mw.select_view("chat")
        self.mw.chat_view.send_text(prompt_text)

    def _generate_image(self):
        prompt, ok = QInputDialog.getText(self, "Generate Image", "Describe the image:")
        if ok and prompt.strip():
            self.mw.select_view("chat")
            self.mw.chat_view.run_tool_and_show(f"Generate an image: {prompt.strip()}",
                                                 lambda: generate_ai_image(prompt.strip()))

    def refresh_theme(self):
        self.sub_lbl.setStyleSheet(f"color:{accent()}; font-size:13px; background:transparent;")
        self.tagline2.setStyleSheet(f"color:{accent()}; font-family:Consolas; font-size:15px; font-weight:bold; background:transparent;")
        self.reactor.refresh_theme()
        self.ask_row.setStyleSheet(f"""
            QFrame#glassFrame {{ background: rgba(10,15,25,215); border: 1px solid {accent()}; border-radius: 24px; }}
            QFrame#glassFrame QLabel {{ background: transparent; border: none; }}
        """)
        for label, (bar, _) in self.status_bars.items():
            bar.setStyleSheet(f"""
                QProgressBar {{ background:#111827; border-radius:4px; }}
                QProgressBar::chunk {{ background:{accent()}; border-radius:4px; }}
            """)
        self._pick_chip(getattr(self, "_active_chip_label", "General"), active_mode_key)

    def set_greeting(self):
        h = datetime.now().hour
        part = "Morning" if h < 12 else ("Afternoon" if h < 18 else "Evening")
        self.greeting_lbl.setText(f"Good {part}, {USER_NAME}")

    def _classify_activity(self, text):
        t = (text or "").lower()
        if "screenshot" in t:
            return "📸", "#f59e0b"
        if any(k in t for k in ["error", "fail", "close", "clos"]):
            return "❌", "#ef4444"
        if any(k in t for k in ["voice", "mic", "listen"]):
            return "🎤", "#a855f7"
        if any(k in t for k in ["research", "wiki", "search", "scrape", "github"]):
            return "🔍", "#38bdf8"
        if any(k in t for k in ["volume", "brightness", "system", "status", "timer", "remind"]):
            return "✅", "#10b981"
        return "💬", "#94a3b8"

    def refresh(self):
        self.set_greeting()
        self.quote_lbl.setText(f'"{random.choice(JARVIS_QUOTES)}"')
        while self.activity_list_lay.count():
            item = self.activity_list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        rows = db_recent_activity(6)
        if not rows:
            lbl = QLabel("No activity yet.")
            lbl.setStyleSheet("color:#64748b; font-size:10px; background:transparent;")
            self.activity_list_lay.addWidget(lbl)
        else:
            for role, content, ts in rows:
                row = QHBoxLayout()
                icon, color = self._classify_activity(content)
                icon_lbl = QLabel(icon)
                icon_lbl.setFixedWidth(18)
                icon_lbl.setStyleSheet(f"color:{color}; font-size:11px; background:transparent;")
                row.addWidget(icon_lbl)
                short = (content[:28] + "…") if len(content) > 28 else (content or "[file]")
                lbl = QLabel(short)
                lbl.setStyleSheet("color:#cbd5e1; font-size:10px; background:transparent;")
                mins_ago = max(0, int((time.time() - ts) / 60))
                when = f"{mins_ago}m ago" if mins_ago < 60 else f"{mins_ago // 60}h ago"
                when_lbl = QLabel(when)
                when_lbl.setStyleSheet("color:#475569; font-size:9px; background:transparent;")
                row.addWidget(lbl, 1)
                row.addWidget(when_lbl)
                self.activity_list_lay.addLayout(row)

    def _animate_bar(self, bar, target_val):
        """Smoothly slides a QProgressBar to its new value instead of
        snapping — QProgressBar's 'value' is a real animatable Qt property."""
        target_val = max(0, min(100, int(target_val)))
        anim = getattr(bar, "_anim", None)
        if anim is None:
            anim = QPropertyAnimation(bar, b"value", bar)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            bar._anim = anim
        anim.stop()
        anim.setDuration(500)
        anim.setStartValue(bar.value())
        anim.setEndValue(target_val)
        anim.start()

    def refresh_status_bars(self):
        try:
            if psutil:
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
                disk = psutil.disk_usage(os.path.abspath(os.sep)).percent
                for label, val in [("CPU", cpu), ("RAM", ram), ("Disk", disk)]:
                    bar, val_lbl = self.status_bars[label]
                    self._animate_bar(bar, val)
                    val_lbl.setText(f"{val:.0f}%")

                try:
                    battery = psutil.sensors_battery()
                    if battery:
                        plugged = " (chg)" if battery.power_plugged else ""
                        self.battery_val_lbl.setText(f"{int(battery.percent)}%{plugged}")
                    else:
                        self.battery_val_lbl.setText("N/A")
                except Exception:
                    self.battery_val_lbl.setText("N/A")

                try:
                    counters = psutil.net_io_counters()
                    now = time.time()
                    total_bytes = counters.bytes_sent + counters.bytes_recv
                    if self._net_prev["bytes"] is not None and self._net_prev["t"] is not None:
                        dt = max(0.5, now - self._net_prev["t"])
                        mbps = ((total_bytes - self._net_prev["bytes"]) * 8 / 1_000_000) / dt
                        self.network_val_lbl.setText(f"{mbps:,.1f} Mbps")
                    self._net_prev["bytes"] = total_bytes
                    self._net_prev["t"] = now
                except Exception:
                    self.network_val_lbl.setText("N/A")
        except Exception:
            pass


# ==========================================
# 15. CHAT INPUT (Enter=send, Shift+Enter=newline)
# ==========================================
class ChatInput(QTextEdit):
    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

# ==========================================
# 16. CHAT VIEW
# ==========================================
class ChatView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self.setAcceptDrops(True)
        self.sessions = {}
        self.current_session_id = None
        self.attached_file_path = None
        self.is_listening = False
        self.sending = False
        self.sparkle = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)

        top = QHBoxLayout()
        title = QLabel("💬 Conversation")
        title.setStyleSheet(f"color:{accent()}; font-weight:bold; font-size:13px; background:transparent;")
        self.title_lbl = title
        top.addWidget(title)
        top.addStretch()
        new_btn = ghost_button("✦ New Chat")
        new_btn.clicked.connect(self.init_new_chat)
        sessions_btn = ghost_button("🗂 Sessions ▾")
        sessions_btn.clicked.connect(self.open_sessions_menu)
        self.sessions_btn = sessions_btn
        top.addWidget(sessions_btn)
        top.addWidget(new_btn)
        root.addLayout(top)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setStyleSheet("QScrollArea{background:rgba(8,12,20,180); border:1px solid rgba(148,163,184,40); border-radius:10px;}")
        chat_content = QWidget()
        chat_content.setStyleSheet("background:transparent;")
        self.chat_layout = QVBoxLayout(chat_content)
        self.chat_layout.addStretch(0)
        self.chat_scroll.setWidget(chat_content)
        root.addWidget(self.chat_scroll, 1)

        self.preview_frame = glass_frame(radius=6, bg="rgba(17,24,39,220)")
        self.preview_frame.setFixedHeight(30)
        pv_lay = QHBoxLayout(self.preview_frame)
        pv_lay.setContentsMargins(10, 2, 6, 2)
        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet(f"color:{accent()}; font-size:11px; background:transparent;")
        pv_lay.addWidget(self.preview_label)
        pv_lay.addStretch()
        rm_btn = QPushButton("✕")
        rm_btn.setFixedSize(18, 18)
        rm_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        rm_btn.setStyleSheet("QPushButton{background:#7f1d1d;color:white;border:none;border-radius:4px;font-size:10px;}"
                              "QPushButton:hover{background:#991b1b;}")
        rm_btn.clicked.connect(self.remove_attachment)
        pv_lay.addWidget(rm_btn)
        self.preview_frame.hide()
        root.addWidget(self.preview_frame)

        input_wrapper = glass_frame(radius=14, bg="rgba(10,15,25,220)")
        iw_lay = QHBoxLayout(input_wrapper)
        iw_lay.setContentsMargins(8, 6, 8, 6)

        attach_btn = ghost_button("📎", height=32)
        attach_btn.setFixedWidth(38)
        attach_btn.clicked.connect(self.attach_file)
        iw_lay.addWidget(attach_btn)

        mic_btn = ghost_button("🎙️", height=32)
        mic_btn.setFixedWidth(38)
        mic_btn.clicked.connect(self.trigger_voice_input)
        self.mic_btn = mic_btn
        iw_lay.addWidget(mic_btn)

        mode_btn = ghost_button("💬 General ▾", height=32)
        mode_btn.clicked.connect(self.open_mode_menu)
        self.mode_trigger_btn = mode_btn
        iw_lay.addWidget(mode_btn)

        self.user_entry = ChatInput()
        self.user_entry.setFixedHeight(38)
        self.user_entry.setStyleSheet("QTextEdit{border:none;background:transparent;color:#f8fafc;font-size:12px;}")
        self.user_entry.send_requested.connect(self.handle_send)
        iw_lay.addWidget(self.user_entry, 1)

        self.send_btn = gradient_button("➔", height=34, radius=8)
        self.send_btn.setFixedWidth(44)
        self.send_btn.clicked.connect(self.handle_send)
        iw_lay.addWidget(self.send_btn)

        root.addWidget(input_wrapper)

        bridge.chat_status.connect(self._on_status)
        bridge.chat_done.connect(self._on_done)
        bridge.voice_captured.connect(self._on_voice_captured)

    # ---------- bubble helpers ----------
    def _add_bubble(self, widget):
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, widget)
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(260)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        widget._fade_anim = anim
        QTimer.singleShot(50, self.scroll_to_bottom)

    def _remove_widget(self, widget):
        self.chat_layout.removeWidget(widget)
        widget.deleteLater()

    def scroll_to_bottom(self):
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_chat_display(self):
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # ---------- attachment ----------
    def attach_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "",
                                               "Supported (*.png *.jpg *.jpeg *.webp *.bmp *.pdf *.txt)")
        if path:
            self.attached_file_path = path
            self.preview_label.setText(f"📎 Attached: {os.path.basename(path)[:26]}")
            self.preview_frame.show()

    def remove_attachment(self):
        self.attached_file_path = None
        self.preview_frame.hide()

    # ---------- drag & drop file attach ----------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if not path or not os.path.isfile(path):
            return
        self.attached_file_path = path
        self.preview_label.setText(f"📎 Attached: {os.path.basename(path)[:26]}")
        self.preview_frame.show()
        notify(f"Attached: {os.path.basename(path)}", "info")
        event.acceptProposedAction()

    # ---------- mode ----------
    def open_mode_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background:#090e17; color:#e2e8f0; border:1px solid {accent()}; border-radius:8px; padding:4px; }}
            QMenu::item {{ padding:8px 18px; border-radius:6px; }}
            QMenu::item:selected {{ background:{primary()}; }}
        """)
        for label, mode_key in [("💬 General", "General"), ("📖 Research", "Research"),
                                 ("</> Coder", "Coder"), ("🎨 Creative", "Creative"),
                                 ("⚡ Automation", "Automation")]:
            act = menu.addAction(label)
            act.triggered.connect(lambda checked=False, m=mode_key: self.select_mode(m))
        menu.exec(self.mode_trigger_btn.mapToGlobal(QPoint(0, self.mode_trigger_btn.height())))

    def select_mode(self, mode_key):
        global active_mode_key
        active_mode_key = mode_key
        self.set_mode_badge(mode_key)

    def set_mode_badge(self, mode_key):
        self.mode_trigger_btn.setText(f"{MODE_CONFIGS[mode_key]['badge']} ▾")

    # ---------- voice ----------
    def trigger_voice_input(self):
        if not sr:
            self.user_entry.insertPlainText(" [Mic library 'SpeechRecognition' missing]")
            return
        if self.is_listening:
            return
        self.is_listening = True
        self.mic_btn.setText("🛑")

        def _listen():
            r = sr.Recognizer()
            try:
                with sr.Microphone() as source:
                    r.adjust_for_ambient_noise(source, duration=0.6)
                    audio = r.listen(source, timeout=6, phrase_time_limit=10)
                    text = r.recognize_google(audio)
                    if text:
                        bridge.voice_captured.emit(text)
            except Exception:
                pass
            finally:
                QTimer.singleShot(0, self._reset_mic)

        threading.Thread(target=_listen, daemon=True).start()

    def _reset_mic(self):
        self.is_listening = False
        self.mic_btn.setText("🎙️")

    def _on_voice_captured(self, text):
        cur = self.user_entry.toPlainText().strip()
        self.user_entry.setPlainText(f"{cur} {text}".strip())
        cursor = self.user_entry.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.user_entry.setTextCursor(cursor)

    # ---------- sessions ----------
    def load_sessions_from_db(self):
        rows = db_load_sessions()
        for row in rows:
            s_id, title, created_at = row[0], row[1], row[2]
            is_pinned = row[3] if len(row) > 3 else 0
            self.sessions[s_id] = {"title": title, "created_at": created_at, "is_pinned": is_pinned}
        if rows:
            self.current_session_id = rows[0][0]
            self.load_session_into_chat(self.current_session_id)
        else:
            self.init_new_chat()

    def init_new_chat(self):
        s_id = f"session_{int(time.time()*1000)}"
        title = f"Chat {len(self.sessions) + 1}"
        created_at = time.time()
        self.sessions[s_id] = {"title": title, "created_at": created_at, "is_pinned": 0}
        self.current_session_id = s_id
        db_save_session(s_id, title, created_at, 0)
        self.clear_chat_display()
        welcome_msg = (
            "J.A.R.V.I.S. Dashboard Online.\n\n"
            "• Type '@' or use the mode menu to switch domains\n"
            "• Click 🎙️ for Voice-to-Text Input\n"
            "• Try: 'Set volume to 50%', 'Look at my screen', or 'Set timer for 1 min'\n\n"
            "Enter your command to proceed."
        )
        self._add_bubble(JarvisBubble(welcome_msg))
        db_save_message(s_id, "jarvis", welcome_msg)

    def load_session_into_chat(self, s_id):
        self.clear_chat_display()
        for role, content, attachment in db_load_messages(s_id):
            if role == "user":
                self._add_bubble(UserBubble(content, attachment))
            else:
                self._add_bubble(JarvisBubble(content))

    def switch_session(self, s_id):
        self.current_session_id = s_id
        self.load_session_into_chat(s_id)

    def rename_session(self, s_id, new_title):
        if s_id not in self.sessions:
            return
        clean = (new_title or "").strip()[:40]
        if not clean:
            return
        self.sessions[s_id]["title"] = clean
        db_update_session_title(s_id, clean)

    def toggle_pin(self, s_id):
        if s_id not in self.sessions:
            return
        cur = self.sessions[s_id].get("is_pinned", 0)
        new_val = 0 if cur else 1
        self.sessions[s_id]["is_pinned"] = new_val
        db_toggle_pin(s_id, new_val)

    def delete_session(self, s_id):
        if s_id not in self.sessions:
            return
        db_delete_session(s_id)
        del self.sessions[s_id]
        if self.current_session_id == s_id:
            if self.sessions:
                next_id = sorted(self.sessions.items(),
                                  key=lambda kv: kv[1].get("created_at", 0), reverse=True)[0][0]
                self.switch_session(next_id)
            else:
                self.init_new_chat()

    def open_sessions_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background:#090e17; color:#e2e8f0; border:1px solid {accent()}; border-radius:8px; padding:4px; }}
            QMenu::item {{ padding:7px 18px; border-radius:6px; }}
            QMenu::item:selected {{ background:{primary()}; }}
        """)
        sorted_sessions = sorted(self.sessions.items(),
                                  key=lambda item: (item[1].get("is_pinned", 0), item[1].get("created_at", 0)),
                                  reverse=True)
        if not sorted_sessions:
            menu.addAction("No chats yet").setEnabled(False)
        for s_id, data in sorted_sessions[:20]:
            marker = "● " if s_id == self.current_session_id else ""
            act = menu.addAction(f"{marker}{data['title'][:30]}")
            act.triggered.connect(lambda checked=False, sid=s_id: self.switch_session(sid))
        menu.exec(self.sessions_btn.mapToGlobal(QPoint(0, self.sessions_btn.height())))

    # ---------- send / dispatch ----------
    def send_text(self, text):
        self.user_entry.setPlainText(text)
        self.handle_send()

    def run_tool_and_show(self, user_label: str, fn):
        """Shows user_label as a chat turn, runs fn() in the background, and
        displays its return value as a JARVIS reply — bypassing the AI
        entirely. Used by Quick Actions (e.g. Generate Image) elsewhere in
        the app so the result is deterministic and instant instead of
        depending on the AI choosing to call the right tool."""
        if self.sending:
            notify("Still working on the previous request — try again in a moment.", "info")
            return
        self._add_bubble(UserBubble(user_label, ""))
        db_save_message(self.current_session_id, "user", user_label, "")
        self.sending = True
        self.send_btn.setEnabled(False)
        self.sparkle = SparkleIndicator()
        self.sparkle.set_status("Working...")
        self._add_bubble(self.sparkle)

        def _worker():
            result = fn()
            bridge.chat_done.emit(result, True)

        threading.Thread(target=_worker, daemon=True).start()

    def handle_send(self):
        if self.sending:
            return
        text = self.user_entry.toPlainText().strip()
        file_path = self.attached_file_path
        if not text and not file_path:
            return
        selected_mode = active_mode_key
        beep(1800, 40)

        sess = self.sessions[self.current_session_id]
        if sess["title"].startswith("Chat ") and text:
            new_title = text[:26]
            sess["title"] = new_title
            db_update_session_title(self.current_session_id, new_title)

        file_name = os.path.basename(file_path) if file_path else ""
        self._add_bubble(UserBubble(text, file_name))
        db_save_message(self.current_session_id, "user", text, file_name)

        self.user_entry.clear()
        if self.attached_file_path:
            self.remove_attachment()

        self.sending = True
        self.send_btn.setEnabled(False)
        self.sparkle = SparkleIndicator()
        self._add_bubble(self.sparkle)

        threading.Thread(target=self._worker, args=(text, file_path, selected_mode), daemon=True).start()

    def _on_status(self, text):
        if self.sparkle:
            self.sparkle.set_status(text)

    def _on_done(self, reply, ok):
        if self.sparkle:
            self.sparkle.stop()
            self._remove_widget(self.sparkle)
            self.sparkle = None
        self._add_bubble(JarvisBubble(reply))
        db_save_message(self.current_session_id, "jarvis", reply)
        self.sending = False
        self.send_btn.setEnabled(True)
        if self.mw.home_view:
            self.mw.home_view.refresh()

    def _worker(self, text, file_path, selected_mode):
        global gemini_key_index
        t_low = text.lower()
        current_file_path = file_path
        if not current_file_path and any(k in t_low for k in
                                          ["look at my screen", "screen par kya hai", "explain this screen", "what is on my screen"]):
            screen_file = f"live_sight_{int(time.time())}.png"
            ImageGrab.grab().save(screen_file)
            current_file_path = screen_file

        ext = os.path.splitext(current_file_path)[1].lower() if current_file_path else ""
        is_image = ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]
        is_doc = ext in [".pdf", ".txt"]

        success = False
        full_reply = ""
        mode_data = MODE_CONFIGS.get(selected_mode, MODE_CONFIGS["Automation"])
        active_prompt = mode_data["system_prompt"]
        active_tools = mode_data["tools"]

        if t_low.startswith("@youtube") or "play on youtube" in t_low or ("play" in t_low and "youtube" in t_low):
            bridge.chat_status.emit(f"{CURRENT_PERSONA} playing on YouTube...")
            q = text.replace("@YouTube", "").replace("@youtube", "").replace("play", "").replace("on youtube", "").strip()
            full_reply = play_youtube_video(q if q else "top hits")
            success = True
        elif t_low.startswith("@whatsapp") or "whatsapp" in t_low:
            bridge.chat_status.emit(f"{CURRENT_PERSONA} automating WhatsApp...")
            full_reply = open_whatsapp(text)
            success = True
        elif "volume" in t_low or "mute" in t_low or "unmute" in t_low:
            bridge.chat_status.emit(f"{CURRENT_PERSONA} adjusting audio...")
            full_reply = set_system_volume(text)
            success = True
        elif "brightness" in t_low:
            bridge.chat_status.emit(f"{CURRENT_PERSONA} adjusting brightness...")
            full_reply = set_screen_brightness(text)
            success = True
        elif any(k in t_low for k in ["timer", "remind me", "reminder"]):
            bridge.chat_status.emit(f"{CURRENT_PERSONA} scheduling timer...")
            full_reply = set_reminder_timer(text)
            success = True
        elif t_low.startswith("@github"):
            bridge.chat_status.emit(f"{CURRENT_PERSONA} querying GitHub...")
            q = text.replace("@GitHub", "").replace("@github", "").strip()
            full_reply = search_github(q if q else "trending")
            success = True
        elif t_low.startswith("@qr_code") or ("generate qr" in t_low or "create qr" in t_low):
            bridge.chat_status.emit(f"{CURRENT_PERSONA} generating QR Code...")
            q = text.replace("@QR_Code", "").replace("@qr_code", "").replace("generate qr for", "").replace("create qr for", "").strip()
            full_reply = generate_qr_code(q if q else "https://google.com")
            success = True
        elif t_low.startswith("@gmail") or ("open" in t_low and "gmail" in t_low):
            bridge.chat_status.emit(f"{CURRENT_PERSONA} opening Gmail...")
            full_reply = open_or_send_gmail()
            success = True
        elif "screenshot" in t_low and not is_image:
            bridge.chat_status.emit(f"{CURRENT_PERSONA} capturing Screenshot...")
            full_reply = take_screenshot()
            success = True
        elif "close" in t_low and ("cmd" in t_low or "terminal" in t_low):
            bridge.chat_status.emit(f"{CURRENT_PERSONA} closing terminal...")
            full_reply = close_application("cmd")
            success = True

        if not success and not GEMINI_KEYS:
            full_reply = "⚠️ No Gemini API key configured yet. Go to Settings → API Keys and add at least one key."
            success = True

        if not success:
            bridge.chat_status.emit(f"{CURRENT_PERSONA} analyzing intent...")
            if is_image:
                keys_snapshot = list(GEMINI_KEYS)
                for attempt in range(len(keys_snapshot)):
                    current_key = keys_snapshot[gemini_key_index % len(keys_snapshot)]
                    try:
                        client_instance = genai.Client(api_key=current_key)
                        bridge.chat_status.emit(f"{CURRENT_PERSONA} analyzing image context...")
                        img_obj = Image.open(current_file_path)
                        prompt = text if text else "Analyze this image and explain in detail."
                        res = client_instance.models.generate_content(
                            model="gemini-2.5-flash", contents=[img_obj, prompt],
                            config=types.GenerateContentConfig(system_instruction=active_prompt))
                        if hasattr(res, "text") and res.text:
                            full_reply = res.text
                        elif hasattr(res, "candidates") and res.candidates:
                            parts = res.candidates[0].content.parts
                            tp = [p.text for p in parts if hasattr(p, "text") and p.text]
                            if tp:
                                full_reply = "\n".join(tp)
                        if full_reply:
                            success = True
                            break
                    except Exception as e:
                        img_err = e
                        log_error(f"[image analysis key #{gemini_key_index + 1}]: {e}")
                    gemini_key_index = (gemini_key_index + 1) % len(keys_snapshot)
            else:
                final_prompt = text
                if is_doc:
                    bridge.chat_status.emit(f"{CURRENT_PERSONA} extracting document...")
                    doc_text = extract_file_content(current_file_path)
                    final_prompt = f"DOCUMENT CONTEXT:\n{doc_text}\n\nQUERY: {text if text else 'Extract key points.'}"
                bridge.chat_status.emit(f"{CURRENT_PERSONA} synthesizing response...")
                full_reply = run_agentic_dispatch(final_prompt, active_prompt, active_tools,
                                                   status_cb=lambda s: bridge.chat_status.emit(s),
                                                   history=build_conversation_context(self.current_session_id, exclude_last_user=True))
                success = bool(full_reply)

        if success and full_reply:
            beep(2400, 30)
        else:
            full_reply = full_reply or (friendly_error(locals().get("img_err")) if locals().get("img_err")
                                        else "⚠️ I couldn't get an answer. Check your internet connection and API key (Settings → Run Health Check).")

        bridge.chat_done.emit(full_reply, success)

    def refresh_theme(self):
        self.title_lbl.setStyleSheet(f"color:{accent()}; font-weight:bold; font-size:13px; background:transparent;")


# ==========================================
# 17. TASKS VIEW (reminders — persisted in SQLite)
# ==========================================
class TasksView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QLabel("🗒️ Tasks & Reminders")
        header.setStyleSheet("color:#f1f5f9; font-size:18px; font-weight:bold; background:transparent;")
        outer.addWidget(header)
        outer.addSpacing(6)
        sub = QLabel("Reminders you create here — or by asking Chat, e.g. \"remind me in 10 min\" — "
                     "are saved and listed below.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        outer.addWidget(sub)
        outer.addSpacing(14)

        add_card = glass_frame()
        ac = QHBoxLayout(add_card)
        self.label_entry = QLineEdit()
        self.label_entry.setPlaceholderText("Reminder text...")
        self.label_entry.setFixedHeight(34)
        self.label_entry.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        ac.addWidget(self.label_entry, 2)
        self.mins_entry = QLineEdit()
        self.mins_entry.setPlaceholderText("Minutes (e.g. 10)")
        self.mins_entry.setFixedHeight(34)
        self.mins_entry.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        ac.addWidget(self.mins_entry, 1)
        add_btn = gradient_button("+ Add Reminder", height=34, radius=8)
        add_btn.clicked.connect(self.add_reminder)
        ac.addWidget(add_btn)
        outer.addWidget(add_card)
        outer.addSpacing(14)

        list_card = glass_frame()
        lc = QVBoxLayout(list_card)
        lc_head = QHBoxLayout()
        lc_title = QLabel("Reminders")
        lc_title.setStyleSheet("color:#f1f5f9; font-weight:bold; font-size:12px; background:transparent;")
        lc_head.addWidget(lc_title)
        lc_head.addStretch()
        refresh_btn = ghost_button("⟳ Refresh", height=26)
        refresh_btn.clicked.connect(self.refresh)
        lc_head.addWidget(refresh_btn)
        lc.addLayout(lc_head)
        self.rows_lay = QVBoxLayout()
        lc.addLayout(self.rows_lay)
        outer.addWidget(list_card)
        outer.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(15000)

    def add_reminder(self):
        label = self.label_entry.text().strip() or "Reminder"
        mins_txt = self.mins_entry.text().strip()
        try:
            mins = float(mins_txt) if mins_txt else 5.0
        except ValueError:
            mins = 5.0
        seconds = max(1, int(mins * 60))
        create_reminder(label, seconds)
        self.label_entry.clear()
        self.mins_entry.clear()
        self.refresh()

    def refresh(self):
        while self.rows_lay.count():
            item = self.rows_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        rows = db_load_reminders()
        if not rows:
            lbl = QLabel("No reminders yet.")
            lbl.setStyleSheet("color:#64748b; font-size:10px; background:transparent;")
            self.rows_lay.addWidget(lbl)
            return
        now = time.time()
        for rid, label, seconds, created_at, fire_at, completed in rows:
            row = QFrame()
            row.setStyleSheet("QFrame{background:#111827; border-radius:6px;}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 6, 6, 6)
            if completed:
                status = "✅ Done"
                color = "#10b981"
            elif fire_at <= now:
                status = "🔔 Due"
                color = "#f59e0b"
            else:
                remaining = int(fire_at - now)
                status = f"⏳ {remaining // 60}m {remaining % 60}s left"
                color = "#94a3b8"
            lbl = QLabel(f"{label}")
            lbl.setStyleSheet("color:#e2e8f0; font-size:11px; background:transparent;")
            rl.addWidget(lbl, 1)
            status_lbl = QLabel(status)
            status_lbl.setStyleSheet(f"color:{color}; font-size:10px; font-weight:bold; background:transparent;")
            rl.addWidget(status_lbl)
            del_btn = QPushButton("✕")
            del_btn.setFixedSize(22, 20)
            del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            del_btn.setStyleSheet("QPushButton{background:#7f1d1d;color:white;border:none;border-radius:4px;font-size:9px;}"
                                   "QPushButton:hover{background:#991b1b;}")
            del_btn.clicked.connect(lambda _, r=rid: self._delete(r))
            rl.addWidget(del_btn)
            self.rows_lay.addWidget(row)

    def _delete(self, rid):
        db_delete_reminder(rid)
        self.refresh()

    def refresh_theme(self):
        pass

# ==========================================
# 18a. VOICE VIEW (dedicated page)
# ==========================================
class VoiceInputCapture(QObject):
    finished = pyqtSignal(str)

class VoiceView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self.is_listening = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QLabel("🎙️ Voice")
        header.setStyleSheet("color:#f1f5f9; font-size:18px; font-weight:bold; background:transparent;")
        outer.addWidget(header)
        sub = QLabel("Speak a command — J.A.R.V.I.S. transcribes it here, then you can edit it before sending to Chat.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        outer.addWidget(sub)
        outer.addSpacing(16)

        card = glass_frame()
        cl = QVBoxLayout(card)

        mic_row = QHBoxLayout()
        mic_row.addStretch()
        self.mic_btn = QPushButton("🎙️")
        self.mic_btn.setFixedSize(72, 72)
        self.mic_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.mic_btn.setStyleSheet(f"""
            QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {primary()}, stop:1 {accent()});
                border:none; border-radius:36px; font-size:26px; }}
            QPushButton:hover {{ background: {accent()}; }}
        """)
        self.mic_btn.clicked.connect(self.toggle_listen)
        mic_row.addWidget(self.mic_btn)
        mic_row.addStretch()
        cl.addLayout(mic_row)

        self.status_lbl = QLabel("Tap the mic to start listening")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet("color:#64748b; font-size:11px; background:transparent; margin-top:8px;")
        cl.addWidget(self.status_lbl)
        cl.addSpacing(14)

        self.text_box = QTextEdit()
        self.text_box.setFixedHeight(100)
        self.text_box.setPlaceholderText("Transcribed text will appear here — you can edit it before sending.")
        self.text_box.setStyleSheet(f"QTextEdit{{background:rgba(17,24,39,210);color:#f8fafc;"
                                     f"border:1px solid rgba(148,163,184,50);"
                                     f"border-radius:10px;font-size:12px;padding:8px;}}"
                                     f"QTextEdit:focus{{border:1px solid {accent()};}}")
        cl.addWidget(self.text_box)

        btn_row = QHBoxLayout()
        clear_btn = ghost_button("Clear", height=34)
        clear_btn.clicked.connect(lambda: self.text_box.clear())
        send_btn = gradient_button("➔ Send to Chat", height=34, radius=8)
        send_btn.clicked.connect(self.send_to_chat)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(send_btn)
        cl.addLayout(btn_row)

        outer.addWidget(card)
        outer.addStretch()

        if not sr:
            self.status_lbl.setText("⚠️ SpeechRecognition package not installed — install it to use voice input.")
            self.mic_btn.setEnabled(False)

    def toggle_listen(self):
        if self.is_listening or not sr:
            return
        self.is_listening = True
        self.mic_btn.setText("🛑")
        self.status_lbl.setText("🎙️ Listening...")
        self._start_mic_pulse()

        sig = VoiceInputCapture(self)
        sig.finished.connect(self._on_captured)

        def _listen():
            r = sr.Recognizer()
            heard = ""
            try:
                with sr.Microphone() as source:
                    r.adjust_for_ambient_noise(source, duration=0.6)
                    audio = r.listen(source, timeout=6, phrase_time_limit=12)
                    heard = r.recognize_google(audio)
            except Exception:
                heard = ""
            sig.finished.emit(heard)

        threading.Thread(target=_listen, daemon=True).start()

    def _start_mic_pulse(self):
        effect = QGraphicsOpacityEffect(self.mic_btn)
        self.mic_btn.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self.mic_btn)
        anim.setDuration(650)
        anim.setLoopCount(-1)
        anim.setKeyValueAt(0.0, 1.0)
        anim.setKeyValueAt(0.5, 0.45)
        anim.setKeyValueAt(1.0, 1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.start()
        self._mic_pulse = anim
        self._mic_pulse_effect = effect

    def _stop_mic_pulse(self):
        if getattr(self, "_mic_pulse", None):
            self._mic_pulse.stop()
            self._mic_pulse = None
        self.mic_btn.setGraphicsEffect(None)

    def _on_captured(self, heard):
        self.is_listening = False
        self.mic_btn.setText("🎙️")
        self._stop_mic_pulse()
        if heard:
            cur = self.text_box.toPlainText().strip()
            self.text_box.setPlainText(f"{cur} {heard}".strip())
            self.status_lbl.setText("✓ Captured — review and send.")
        else:
            self.status_lbl.setText("Didn't catch that — tap the mic and try again.")

    def send_to_chat(self):
        text = self.text_box.toPlainText().strip()
        if not text:
            return
        self.mw.select_view("chat")
        self.mw.chat_view.send_text(text)
        self.text_box.clear()
        self.status_lbl.setText("Sent to Chat.")

    def refresh_theme(self):
        self.mic_btn.setStyleSheet(f"""
            QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {primary()}, stop:1 {accent()});
                border:none; border-radius:36px; font-size:26px; }}
            QPushButton:hover {{ background: {accent()}; }}
        """)

# ==========================================
# 18b. APPS & CONTROL VIEW (quick launchers)
# ==========================================
class AppsControlView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QLabel("🧩 Apps & Control")
        header.setStyleSheet("color:#f1f5f9; font-size:18px; font-weight:bold; background:transparent;")
        outer.addWidget(header)
        sub = QLabel("One-tap launchers and hardware controls — no need to type a command.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        outer.addWidget(sub)
        outer.addSpacing(16)

        grid_card = glass_frame()
        gc = QGridLayout(grid_card)
        gc.setSpacing(8)

        actions = [
            ("▶️", "#ef4444", "YouTube", self._youtube), ("💬", "#10b981", "WhatsApp", self._whatsapp),
            ("✉️", "#38bdf8", "Gmail", self._gmail), ("🐙", "#e2e8f0", "GitHub", self._github),
            ("📸", "#f59e0b", "Screenshot", self._screenshot), ("📱", "#a855f7", "Generate QR", self._qr),
            ("🔊", "#38bdf8", "Volume 50%", lambda: self._run(lambda: set_system_volume("50"))),
            ("🔇", "#94a3b8", "Mute", lambda: self._run(lambda: set_system_volume("mute"))),
            ("🔆", "#f59e0b", "Brightness 70%", lambda: self._run(lambda: set_screen_brightness("70"))),
            ("🖥️", "#10b981", "System Status", lambda: self._run(get_system_status)),
            ("❌", "#ef4444", "Close App", self._close_app), ("🌐", "#38bdf8", "Scrape URL", self._scrape),
            ("📧", "#ef4444", "Recent Emails", lambda: self._run(gmail_list_recent)),
            ("📁", "#38bdf8", "Drive Files", lambda: self._run(drive_list_recent_files)),
            ("📄", "#10b981", "New Google Doc", self._new_doc),
            ("🖼️", "#ec4899", "Generate Image", self._generate_image),
            ("📅", "#a855f7", "Today's Events", lambda: self._run(lambda: calendar_list_events("1"))),
        ]
        for i, (icon, color, label, handler) in enumerate(actions):
            b = action_tile(icon, color, label, handler, height=50)
            gc.addWidget(b, i // 3, i % 3)
        for c in range(3):
            gc.setColumnStretch(c, 1)
        outer.addWidget(grid_card)
        outer.addSpacing(14)

        result_card = glass_frame()
        rc = QVBoxLayout(result_card)
        rc_title = QLabel("Result")
        rc_title.setStyleSheet("color:#f1f5f9; font-weight:bold; font-size:12px; background:transparent;")
        rc.addWidget(rc_title)
        self.result_container = QVBoxLayout()
        rc.addLayout(self.result_container)
        self._set_text_result("Tap an action above to see its result here.")
        outer.addWidget(result_card)
        outer.addStretch()

    def _clear_result(self):
        while self.result_container.count():
            item = self.result_container.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _set_text_result(self, text):
        self._clear_result()
        box = result_box(height=90)
        box.setPlainText(text)
        self.result_container.addWidget(box)

    def _run(self, fn):
        self._set_text_result("Working...")
        run_bg(self, fn, self._set_text_result)

    def _youtube(self):
        self._run(lambda: play_youtube_video("top hits"))

    def _whatsapp(self):
        self._run(lambda: open_whatsapp(""))

    def _gmail(self):
        self._run(open_or_send_gmail)

    def _github(self):
        q, ok = QInputDialog.getText(self, "GitHub", "Username or topic:")
        if ok and q.strip():
            self._run(lambda: search_github(q.strip()))

    def _screenshot(self):
        self._set_text_result("Capturing...")
        def _do():
            res = take_screenshot()
            QTimer.singleShot(0, lambda: self._show_maybe_image(res))
        threading.Thread(target=_do, daemon=True).start()

    def _qr(self):
        q, ok = QInputDialog.getText(self, "Generate QR Code", "Text or URL:", text="https://google.com")
        if ok and q.strip():
            self._set_text_result("Generating...")
            def _do():
                res = generate_qr_code(q.strip())
                QTimer.singleShot(0, lambda: self._show_maybe_image(res))
            threading.Thread(target=_do, daemon=True).start()

    def _generate_image(self):
        prompt, ok = QInputDialog.getText(self, "Generate Image", "Describe the image:")
        if ok and prompt.strip():
            self._set_text_result("Generating image... this can take a few seconds.")
            def _do():
                res = generate_ai_image(prompt.strip())
                QTimer.singleShot(0, lambda: self._show_maybe_image(res))
            threading.Thread(target=_do, daemon=True).start()

    def _show_maybe_image(self, res):
        self._clear_result()
        if res.startswith("QR_FILE:"):
            add_image_preview(self.result_container, res.split("QR_FILE:", 1)[1].strip(), "QR Code generated:")
        elif res.startswith("SS_FILE:"):
            add_image_preview(self.result_container, res.split("SS_FILE:", 1)[1].strip(), "Screenshot captured:")
        elif res.startswith("IMG_FILE:"):
            add_image_preview(self.result_container, res.split("IMG_FILE:", 1)[1].strip(), "Generated image:")
        else:
            box = result_box(height=90)
            box.setPlainText(res)
            self.result_container.addWidget(box)

    def _close_app(self):
        name, ok = QInputDialog.getText(self, "Close Application", "App name (e.g. chrome, notepad):")
        if not (ok and name.strip()):
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Confirm")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(f"Force-close all '{name.strip()}' processes?")
        confirm.setInformativeText("Any unsaved work in that app will be lost.")
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() == QMessageBox.StandardButton.Yes:
            self._run(lambda: close_application(name.strip()))

    def _scrape(self):
        url, ok = QInputDialog.getText(self, "Scrape Webpage", "URL:")
        if ok and url.strip():
            self._run(lambda: scrape_webpage(url.strip()))

    def _new_doc(self):
        title, ok = QInputDialog.getText(self, "New Google Doc", "Document title:")
        if ok and title.strip():
            self._run(lambda: docs_create_document(title.strip()))

    def refresh_theme(self):
        pass

# ==========================================
# 18c. AUTOMATION VIEW (dedicated page)
# ==========================================
class AutomationView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QLabel("⚡ Automation")
        header.setStyleSheet("color:#f1f5f9; font-size:18px; font-weight:bold; background:transparent;")
        outer.addWidget(header)
        sub = QLabel("Type any automation command — it runs through the same AI + tool pipeline as Chat's "
                     "Automation mode, with full results shown in Chat.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        outer.addWidget(sub)
        outer.addSpacing(16)

        cmd_card = glass_frame()
        cc = QHBoxLayout(cmd_card)
        self.cmd_entry = QLineEdit()
        self.cmd_entry.setPlaceholderText("e.g. Set volume to 60% and take a screenshot")
        self.cmd_entry.setFixedHeight(36)
        self.cmd_entry.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        self.cmd_entry.returnPressed.connect(self.run_command)
        cc.addWidget(self.cmd_entry, 1)
        run_btn = gradient_button("▶ Run in Chat", height=36, radius=8)
        run_btn.clicked.connect(self.run_command)
        cc.addWidget(run_btn)
        outer.addWidget(cmd_card)
        outer.addSpacing(16)

        quick_card = glass_frame()
        qc = QVBoxLayout(quick_card)
        qc_title = QLabel("Instant Actions (no AI call — runs immediately)")
        qc_title.setStyleSheet("color:#f1f5f9; font-weight:bold; font-size:12px; background:transparent;")
        qc.addWidget(qc_title)
        qc_grid = QGridLayout()
        qc_grid.setSpacing(8)
        quick_actions = [
            ("📸", "#f59e0b", "Screenshot", lambda: self._instant(take_screenshot)),
            ("🖥️", "#10b981", "System Status", lambda: self._instant(get_system_status)),
            ("🔊", "#38bdf8", "Volume 50%", lambda: self._instant(lambda: set_system_volume("50"))),
            ("⏱️", "#a855f7", "5 min Timer", lambda: self._instant(lambda: set_reminder_timer("5 min"))),
        ]
        for i, (icon, color, label, handler) in enumerate(quick_actions):
            b = action_tile(icon, color, label, handler, height=48)
            qc_grid.addWidget(b, i // 2, i % 2)
        qc.addLayout(qc_grid)
        outer.addWidget(quick_card)
        outer.addSpacing(14)

        self.result_container = QVBoxLayout()
        outer.addLayout(self.result_container)
        outer.addStretch()

    def run_command(self):
        text = self.cmd_entry.text().strip()
        if not text:
            return
        self.cmd_entry.clear()
        global active_mode_key
        active_mode_key = "Automation"
        self.mw.chat_view.set_mode_badge("Automation")
        self.mw.select_view("chat")
        self.mw.chat_view.send_text(text)

    def _instant(self, fn):
        while self.result_container.count():
            item = self.result_container.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        box = result_box(height=80)
        box.setPlainText("Working...")
        self.result_container.addWidget(box)
        run_bg(self, fn, box.setPlainText)

    def refresh_theme(self):
        pass

# ==========================================
# 18d. FILES & MEMORY VIEW (full session management)
# ==========================================
class FilesView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        top = QHBoxLayout()
        header = QLabel("📁 Files & Memory")
        header.setStyleSheet("color:#f1f5f9; font-size:18px; font-weight:bold; background:transparent;")
        top.addWidget(header)
        top.addStretch()
        new_btn = gradient_button("✦ New Chat", height=32, radius=8)
        new_btn.clicked.connect(self._new_chat)
        top.addWidget(new_btn)
        outer.addLayout(top)

        sub = QLabel("Every chat session is saved here — rename, pin, reopen, or delete any of them.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        outer.addWidget(sub)
        outer.addSpacing(10)

        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("🔍 Search chats...")
        self.search_entry.setFixedHeight(32)
        self.search_entry.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        self.search_entry.textChanged.connect(self.refresh)
        outer.addWidget(self.search_entry)
        outer.addSpacing(10)

        list_card = glass_frame()
        lc = QVBoxLayout(list_card)
        self.rows_lay = QVBoxLayout()
        lc.addLayout(self.rows_lay)
        outer.addWidget(list_card)
        outer.addSpacing(16)

        # ---- Long-Term Memory ----
        mem_card = glass_frame()
        mcl = QVBoxLayout(mem_card)
        mcl.addWidget(self._label("LONG-TERM MEMORY"))
        mnote = QLabel('Facts J.A.R.V.I.S. remembers across every conversation — say "remember that ..." '
                       "in Chat/Voice, or add one here directly.")
        mnote.setWordWrap(True)
        mnote.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
        mcl.addWidget(mnote)
        mem_add_row = QHBoxLayout()
        self.mem_entry = QLineEdit()
        self.mem_entry.setPlaceholderText("e.g. I prefer Hindi replies")
        self.mem_entry.setFixedHeight(32)
        self.mem_entry.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        self.mem_entry.returnPressed.connect(self._add_memory_fact)
        mem_add_row.addWidget(self.mem_entry, 1)
        mem_add_btn = gradient_button("+ Remember", height=32, radius=8)
        mem_add_btn.clicked.connect(self._add_memory_fact)
        mem_add_row.addWidget(mem_add_btn)
        mcl.addLayout(mem_add_row)
        self.mem_rows_lay = QVBoxLayout()
        mcl.addLayout(self.mem_rows_lay)
        outer.addWidget(mem_card)
        self.refresh_memory_facts()
        outer.addStretch()

    def _label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#475569; font-family:Consolas; font-size:10px; font-weight:bold; background:transparent;")
        return lbl

    def _add_memory_fact(self):
        fact = self.mem_entry.text().strip()
        if not fact:
            return
        db_add_memory_fact(fact)
        self.mem_entry.clear()
        self.refresh_memory_facts()

    def _delete_memory_fact(self, fid):
        db_delete_memory_fact(fid)
        self.refresh_memory_facts()

    def refresh_memory_facts(self):
        while self.mem_rows_lay.count():
            item = self.mem_rows_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        facts = db_load_memory_facts()
        if not facts:
            lbl = QLabel("Nothing remembered yet.")
            lbl.setStyleSheet("color:#64748b; font-size:10px; background:transparent;")
            self.mem_rows_lay.addWidget(lbl)
            return
        for fid, fact, created_at in facts:
            row = QFrame()
            row.setStyleSheet("QFrame{background:#111827; border-radius:6px;}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 5, 6, 5)
            lbl = QLabel(fact)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#e2e8f0; font-size:11px; background:transparent;")
            rl.addWidget(lbl, 1)
            del_btn = QPushButton("✕")
            del_btn.setFixedSize(22, 20)
            del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            del_btn.setStyleSheet("QPushButton{background:#7f1d1d;color:white;border:none;border-radius:4px;font-size:9px;}"
                                   "QPushButton:hover{background:#991b1b;}")
            del_btn.clicked.connect(lambda _, i=fid: self._delete_memory_fact(i))
            rl.addWidget(del_btn)
            self.mem_rows_lay.addWidget(row)

    def _new_chat(self):
        self.mw.chat_view.init_new_chat()
        self.mw.select_view("chat")

    def _open(self, s_id):
        self.mw.chat_view.switch_session(s_id)
        self.mw.select_view("chat")

    def _rename(self, s_id, current_title):
        new_title, ok = QInputDialog.getText(self, "Rename Chat", "New title:", text=current_title)
        if ok and new_title.strip():
            self.mw.chat_view.rename_session(s_id, new_title)
            self.refresh()

    def _pin(self, s_id):
        self.mw.chat_view.toggle_pin(s_id)
        self.refresh()

    def _delete(self, s_id, title):
        box = QMenu(self)  # lightweight confirm via a tiny popup menu instead of a blocking dialog
        box.setStyleSheet(f"QMenu {{ background:#090e17; color:#e2e8f0; border:1px solid {accent()}; border-radius:8px; padding:4px; }}"
                           "QMenu::item { padding:8px 16px; border-radius:6px; } QMenu::item:selected { background:#7f1d1d; }")
        act = box.addAction(f"🗑️ Confirm delete '{title[:20]}'")
        chosen = box.exec(QCursor.pos())
        if chosen == act:
            self.mw.chat_view.delete_session(s_id)
            self.refresh()

    def refresh(self):
        while self.rows_lay.count():
            item = self.rows_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        sessions = self.mw.chat_view.sessions
        query = self.search_entry.text().strip().lower()
        sorted_sessions = sorted(sessions.items(),
                                  key=lambda kv: (kv[1].get("is_pinned", 0), kv[1].get("created_at", 0)),
                                  reverse=True)
        if query:
            sorted_sessions = [(sid, d) for sid, d in sorted_sessions if query in d["title"].lower()]

        if not sorted_sessions:
            lbl = QLabel("No chats found.")
            lbl.setStyleSheet("color:#64748b; font-size:10px; background:transparent;")
            self.rows_lay.addWidget(lbl)
            return

        current_id = self.mw.chat_view.current_session_id
        for s_id, data in sorted_sessions:
            is_active = (s_id == current_id)
            is_pinned = data.get("is_pinned", 0) == 1
            row = QFrame()
            row.setStyleSheet(f"QFrame{{background:{'#0f1c2e' if is_active else '#111827'}; border-radius:8px;}}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 6, 8, 6)

            pin_icon = "📌 " if is_pinned else ""
            title_lbl = QLabel(f"{pin_icon}{data['title'][:40]}")
            title_lbl.setStyleSheet(f"color:{accent() if is_active else '#e2e8f0'}; font-size:12px; "
                                     f"font-weight:{'bold' if (is_active or is_pinned) else 'normal'}; background:transparent;")
            title_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            title_lbl.mousePressEvent = lambda e, sid=s_id: self._open(sid)
            rl.addWidget(title_lbl, 1)

            pin_btn = QPushButton("📍" if is_pinned else "📌")
            pin_btn.setFixedSize(26, 24)
            pin_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            pin_btn.setStyleSheet("QPushButton{background:transparent;border:none;font-size:12px;}"
                                   "QPushButton:hover{background:#1e293b;border-radius:4px;}")
            pin_btn.clicked.connect(lambda _, sid=s_id: self._pin(sid))
            rl.addWidget(pin_btn)

            rename_btn = QPushButton("✏️")
            rename_btn.setFixedSize(26, 24)
            rename_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            rename_btn.setStyleSheet("QPushButton{background:transparent;border:none;font-size:12px;}"
                                      "QPushButton:hover{background:#1e293b;border-radius:4px;}")
            rename_btn.clicked.connect(lambda _, sid=s_id, t=data["title"]: self._rename(sid, t))
            rl.addWidget(rename_btn)

            del_btn = QPushButton("🗑️")
            del_btn.setFixedSize(26, 24)
            del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            del_btn.setStyleSheet("QPushButton{background:transparent;border:none;font-size:12px;}"
                                   "QPushButton:hover{background:#7f1d1d;border-radius:4px;}")
            del_btn.clicked.connect(lambda _, sid=s_id, t=data["title"]: self._delete(sid, t))
            rl.addWidget(del_btn)

            self.rows_lay.addWidget(row)

    def refresh_theme(self):
        self.refresh()

# ==========================================
# 18e. RESEARCH VIEW
# ==========================================
class ResearchView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QLabel("🔎 Research")
        header.setStyleSheet("color:#f1f5f9; font-size:18px; font-weight:bold; background:transparent;")
        outer.addWidget(header)
        sub = QLabel("Search Wikipedia or pull the text of any webpage directly — no need to open Chat.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        outer.addWidget(sub)
        outer.addSpacing(16)

        wiki_card = glass_frame()
        wc = QHBoxLayout(wiki_card)
        self.wiki_entry = QLineEdit()
        self.wiki_entry.setPlaceholderText("Search Wikipedia for...")
        self.wiki_entry.setFixedHeight(34)
        self.wiki_entry.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        self.wiki_entry.returnPressed.connect(self._search_wiki)
        wc.addWidget(self.wiki_entry, 1)
        wiki_btn = gradient_button("Search", height=34, radius=8)
        wiki_btn.clicked.connect(self._search_wiki)
        wc.addWidget(wiki_btn)
        outer.addWidget(wiki_card)
        outer.addSpacing(10)

        scrape_card = glass_frame()
        sc = QHBoxLayout(scrape_card)
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Paste a URL to scrape...")
        self.url_entry.setFixedHeight(34)
        self.url_entry.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        self.url_entry.returnPressed.connect(self._scrape)
        sc.addWidget(self.url_entry, 1)
        scrape_btn = ghost_button("Scrape", height=34)
        scrape_btn.clicked.connect(self._scrape)
        sc.addWidget(scrape_btn)
        outer.addWidget(scrape_card)
        outer.addSpacing(14)

        result_card = glass_frame()
        rc = QVBoxLayout(result_card)
        rc_title = QLabel("Result")
        rc_title.setStyleSheet("color:#f1f5f9; font-weight:bold; font-size:12px; background:transparent;")
        rc.addWidget(rc_title)
        self.result_box = result_box(height=220)
        rc.addWidget(self.result_box)
        outer.addWidget(result_card)
        outer.addStretch()

    def _search_wiki(self):
        q = self.wiki_entry.text().strip()
        if not q:
            return
        set_result_box(self.result_box, "Searching Wikipedia...")
        run_bg(self, lambda: search_wikipedia(q), lambda r: set_result_box(self.result_box, r))

    def _scrape(self):
        url = self.url_entry.text().strip()
        if not url:
            return
        set_result_box(self.result_box, "Scraping...")
        run_bg(self, lambda: scrape_webpage(url), lambda r: set_result_box(self.result_box, r))

    def refresh_theme(self):
        pass

# ==========================================
# 18f. TOOLS VIEW (crypto / forex / lot-size / QR)
# ==========================================
class ToolsView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QLabel("🛠️ Tools")
        header.setStyleSheet("color:#f1f5f9; font-size:18px; font-weight:bold; background:transparent;")
        outer.addWidget(header)
        sub = QLabel("Quick calculators and generators — crypto prices, currency conversion, "
                     "position sizing, and QR codes.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        outer.addWidget(sub)
        outer.addSpacing(16)

        # --- Crypto price ---
        crypto_card = glass_frame()
        cc = QVBoxLayout(crypto_card)
        cc.addWidget(self._label("CRYPTO PRICE"))
        cc_row = QHBoxLayout()
        self.crypto_entry = QLineEdit()
        self.crypto_entry.setPlaceholderText("Symbol, e.g. BTCUSDT")
        self.crypto_entry.setFixedHeight(32)
        self.crypto_entry.setStyleSheet(self._entry_style())
        cc_row.addWidget(self.crypto_entry, 1)
        crypto_btn = gradient_button("Check", height=32, radius=8)
        crypto_btn.clicked.connect(self._check_crypto)
        cc_row.addWidget(crypto_btn)
        cc.addLayout(cc_row)
        self.crypto_result = result_box(height=50)
        cc.addWidget(self.crypto_result)
        outer.addWidget(crypto_card)
        outer.addSpacing(10)

        # --- Forex convert ---
        forex_card = glass_frame()
        fc = QVBoxLayout(forex_card)
        fc.addWidget(self._label("CURRENCY CONVERTER"))
        fc_row = QHBoxLayout()
        self.amt_entry = QLineEdit()
        self.amt_entry.setPlaceholderText("Amount")
        self.amt_entry.setFixedHeight(32)
        self.amt_entry.setStyleSheet(self._entry_style())
        self.from_entry = QLineEdit()
        self.from_entry.setPlaceholderText("From (USD)")
        self.from_entry.setFixedHeight(32)
        self.from_entry.setStyleSheet(self._entry_style())
        self.to_entry = QLineEdit()
        self.to_entry.setPlaceholderText("To (INR)")
        self.to_entry.setFixedHeight(32)
        self.to_entry.setStyleSheet(self._entry_style())
        fc_row.addWidget(self.amt_entry, 1)
        fc_row.addWidget(self.from_entry, 1)
        fc_row.addWidget(self.to_entry, 1)
        forex_btn = gradient_button("Convert", height=32, radius=8)
        forex_btn.clicked.connect(self._convert_forex)
        fc_row.addWidget(forex_btn)
        fc.addLayout(fc_row)
        self.forex_result = result_box(height=50)
        fc.addWidget(self.forex_result)
        outer.addWidget(forex_card)
        outer.addSpacing(10)

        # --- Lot size calculator ---
        lot_card = glass_frame()
        lcc = QVBoxLayout(lot_card)
        lcc.addWidget(self._label("FOREX LOT SIZE CALCULATOR"))
        lot_row = QHBoxLayout()
        self.balance_entry = QLineEdit()
        self.balance_entry.setPlaceholderText("Balance ($)")
        self.balance_entry.setFixedHeight(32)
        self.balance_entry.setStyleSheet(self._entry_style())
        self.risk_entry = QLineEdit()
        self.risk_entry.setPlaceholderText("Risk %")
        self.risk_entry.setFixedHeight(32)
        self.risk_entry.setStyleSheet(self._entry_style())
        self.sl_entry = QLineEdit()
        self.sl_entry.setPlaceholderText("Stop Loss (pips)")
        self.sl_entry.setFixedHeight(32)
        self.sl_entry.setStyleSheet(self._entry_style())
        lot_row.addWidget(self.balance_entry, 1)
        lot_row.addWidget(self.risk_entry, 1)
        lot_row.addWidget(self.sl_entry, 1)
        lot_btn = gradient_button("Calculate", height=32, radius=8)
        lot_btn.clicked.connect(self._calc_lot)
        lot_row.addWidget(lot_btn)
        lcc.addLayout(lot_row)
        self.lot_result = result_box(height=50)
        lcc.addWidget(self.lot_result)
        outer.addWidget(lot_card)
        outer.addSpacing(10)

        # --- QR code generator ---
        qr_card = glass_frame()
        qc = QVBoxLayout(qr_card)
        qc.addWidget(self._label("QR CODE GENERATOR"))
        qr_row = QHBoxLayout()
        self.qr_entry = QLineEdit()
        self.qr_entry.setPlaceholderText("Text or URL")
        self.qr_entry.setFixedHeight(32)
        self.qr_entry.setStyleSheet(self._entry_style())
        qr_row.addWidget(self.qr_entry, 1)
        qr_btn = gradient_button("Generate", height=32, radius=8)
        qr_btn.clicked.connect(self._gen_qr)
        qr_row.addWidget(qr_btn)
        qc.addLayout(qr_row)
        self.qr_result_container = QVBoxLayout()
        qc.addLayout(self.qr_result_container)
        outer.addWidget(qr_card)
        outer.addStretch()

    def _label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#475569; font-family:Consolas; font-size:10px; font-weight:bold; background:transparent;")
        return lbl

    def _entry_style(self):
        return f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}"

    def _check_crypto(self):
        sym = self.crypto_entry.text().strip() or "BTCUSDT"
        set_result_box(self.crypto_result, "Checking...")
        run_bg(self, lambda: get_crypto_price(sym), lambda r: set_result_box(self.crypto_result, r))

    def _convert_forex(self):
        amt = self.amt_entry.text().strip() or "1"
        fr = self.from_entry.text().strip() or "USD"
        to = self.to_entry.text().strip() or "INR"
        set_result_box(self.forex_result, "Converting...")
        run_bg(self, lambda: convert_forex_currency(amt, fr, to), lambda r: set_result_box(self.forex_result, r))

    def _calc_lot(self):
        bal = self.balance_entry.text().strip() or "10000"
        risk = self.risk_entry.text().strip() or "1.0"
        sl = self.sl_entry.text().strip() or "30"
        set_result_box(self.lot_result, calculate_lot_size(bal, risk, sl))

    def _gen_qr(self):
        q = self.qr_entry.text().strip() or "https://google.com"
        while self.qr_result_container.count():
            item = self.qr_result_container.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        box = result_box(height=40)
        box.setPlainText("Generating...")
        self.qr_result_container.addWidget(box)

        def _done(res):
            while self.qr_result_container.count():
                item = self.qr_result_container.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            if res.startswith("QR_FILE:"):
                add_image_preview(self.qr_result_container, res.split("QR_FILE:", 1)[1].strip(), "QR Code generated:")
            else:
                b = result_box(height=50)
                b.setPlainText(res)
                self.qr_result_container.addWidget(b)

        run_bg(self, lambda: generate_qr_code(q), _done)

    def refresh_theme(self):
        pass

# ==========================================
# 18. SETTINGS VIEW
# ==========================================
class SettingsView(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self.pending_new_keys = []

        # Settings has many cards — put them in a scroll area so they keep
        # their natural height instead of being squashed on top of each other.
        _page = QVBoxLayout(self)
        _page.setContentsMargins(0, 0, 0, 0)
        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(QFrame.Shape.NoFrame)
        _scroll.setStyleSheet("QScrollArea{background:transparent; border:none;} "
                              "QScrollArea > QWidget > QWidget{background:transparent;}")
        _scroll.viewport().setAutoFillBackground(False)
        _inner = QWidget()
        _inner.setAutoFillBackground(False)
        _scroll.setWidget(_inner)
        _page.addWidget(_scroll)

        outer = QVBoxLayout(_inner)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        header = QLabel("⚙️ Settings")
        header.setStyleSheet("color:#f1f5f9; font-size:18px; font-weight:bold; background:transparent;")
        outer.addWidget(header)
        outer.addSpacing(14)

        # ---- Theme ----
        theme_card = glass_frame()
        tc = QVBoxLayout(theme_card)
        tc.addWidget(self._section_label("AI PERSONA"))
        self.persona_capsules = PersonaCapsules(self.mw)
        tc.addWidget(self.persona_capsules)
        pn = QLabel("Each persona has its own look and voice — all three have the exact same abilities. "
                    'Say "Hey Jarvis", "Hey Friday" or "Hey Eleven" to switch by voice.')
        pn.setWordWrap(True)
        pn.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
        tc.addWidget(pn)
        self.theme_btns = {}  # old per-color theme buttons were replaced by personas
        outer.addWidget(theme_card)
        outer.addSpacing(12)
        self._refresh_theme_buttons()

        # ---- Health check ----
        hc_card = glass_frame()
        hcl = QHBoxLayout(hc_card)
        hc_txt = QVBoxLayout()
        hc_txt.addWidget(self._section_label("SYSTEM HEALTH"))
        hc_note = QLabel("Checks internet, API key, voice, microphone, Chrome, Google login and more — and tells you exactly how to fix anything that's wrong.")
        hc_note.setWordWrap(True)
        hc_note.setStyleSheet("color:#64748b; font-size:10px; background:transparent;")
        hc_txt.addWidget(hc_note)
        hcl.addLayout(hc_txt, 1)
        hc_btn = gradient_button("🩺 Run Health Check", height=38, radius=10)
        hc_btn.setFixedWidth(180)
        hc_btn.clicked.connect(lambda: HealthCheckDialog(self.mw).exec())
        hcl.addWidget(hc_btn)
        outer.addWidget(hc_card)
        outer.addSpacing(12)

        # ---- Name ----
        name_card = glass_frame()
        nc = QVBoxLayout(name_card)
        nc.addWidget(self._section_label("YOUR NAME"))
        name_row = QHBoxLayout()
        self.name_edit = QLineEdit(USER_NAME)
        self.name_edit.setFixedHeight(32)
        self.name_edit.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        name_row.addWidget(self.name_edit, 1)
        save_name_btn = gradient_button("Save Name", height=32, radius=8)
        save_name_btn.clicked.connect(self.save_name)
        name_row.addWidget(save_name_btn)
        nc.addLayout(name_row)
        self.name_status = QLabel("")
        self.name_status.setStyleSheet("color:#10b981; font-size:10px; background:transparent;")
        nc.addWidget(self.name_status)
        outer.addWidget(name_card)
        outer.addSpacing(12)

        # ---- API Keys ----
        keys_card = glass_frame()
        kc = QVBoxLayout(keys_card)
        kc.addWidget(self._section_label(f"GEMINI API KEYS  (1 to {MAX_KEYS} — instant failover, no error shown)"))
        self.keys_count_lbl = QLabel("")
        self.keys_count_lbl.setStyleSheet("color:#64748b; font-size:10px; font-weight:bold; background:transparent;")
        kc.addWidget(self.keys_count_lbl)
        self.keys_list_lay = QVBoxLayout()
        kc.addLayout(self.keys_list_lay)

        add_row = QHBoxLayout()
        self.new_key_entry = QLineEdit()
        self.new_key_entry.setPlaceholderText("Paste a new Gemini API key here...")
        self.new_key_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_key_entry.setFixedHeight(32)
        self.new_key_entry.setStyleSheet(f"QLineEdit{{background:rgba(17,24,39,210);color:#f8fafc;border:1px solid rgba(148,163,184,50);border-radius:8px;padding:0 12px;}}QLineEdit:focus{{border:1px solid {accent()};}}")
        self.new_key_entry.returnPressed.connect(self.add_key)
        add_row.addWidget(self.new_key_entry, 1)
        eye_btn = ghost_button("👁", height=32)
        eye_btn.setFixedWidth(38)
        eye_btn.clicked.connect(self._toggle_new_key_visibility)
        self._eye_btn = eye_btn
        add_row.addWidget(eye_btn)
        add_btn = gradient_button("+ Add Key", height=32, radius=8)
        add_btn.clicked.connect(self.add_key)
        add_row.addWidget(add_btn)
        kc.addLayout(add_row)

        get_key_row2 = QHBoxLayout()
        get_key_btn2 = QPushButton("🔗 Get a free Gemini API Key")
        get_key_btn2.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        get_key_btn2.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {accent()}; border: none;
                font-size: 10px; font-weight: bold; text-decoration: underline; padding: 4px 0; }}
            QPushButton:hover {{ color: #ffffff; }}
        """)
        get_key_btn2.clicked.connect(lambda: webbrowser.open("https://aistudio.google.com/apikey"))
        get_key_row2.addWidget(get_key_btn2)
        get_key_row2.addStretch()
        kc.addLayout(get_key_row2)

        self.key_status_lbl = QLabel("")
        self.key_status_lbl.setStyleSheet("color:#ef4444; font-size:10px; background:transparent;")
        kc.addWidget(self.key_status_lbl)
        outer.addWidget(keys_card)
        outer.addSpacing(12)

        note = QLabel("Keys are written to jarvis_config.json next to this script (plaintext). Each person running\n"
                       "this app on their own laptop keeps their own name + keys — nothing is shared between\n"
                       "installs. Don't commit jarvis_config.json to git.")
        note.setStyleSheet("color:#f59e0b; font-size:10px; background:transparent;")
        outer.addWidget(note)
        outer.addSpacing(16)

        # ---- Google Workspace ----
        google_card = glass_frame()
        gcl = QVBoxLayout(google_card)
        gcl.addWidget(self._section_label("GOOGLE WORKSPACE (Gmail / Drive / Docs)"))
        g_row = QHBoxLayout()
        self.google_status_lbl = QLabel("")
        self.google_status_lbl.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        g_row.addWidget(self.google_status_lbl, 1)
        self.google_connect_btn = gradient_button("Connect Google Account", height=32, radius=8)
        self.google_connect_btn.clicked.connect(self._google_connect)
        g_row.addWidget(self.google_connect_btn)
        self.google_disconnect_btn = ghost_button("Disconnect", height=32)
        self.google_disconnect_btn.clicked.connect(self._google_disconnect)
        g_row.addWidget(self.google_disconnect_btn)
        gcl.addLayout(g_row)
        gnote = QLabel("Needs credentials.json (from Google Cloud Console, OAuth client → Desktop app)\n"
                       "placed next to this script. First connect opens a browser for you to approve access.")
        gnote.setWordWrap(True)
        gnote.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
        gcl.addWidget(gnote)
        outer.addWidget(google_card)
        self._refresh_google_status()
        outer.addSpacing(16)

        # ---- Permissions (System Automation) ----
        perm_card = glass_frame()
        pcl = QVBoxLayout(perm_card)
        pcl.addWidget(self._section_label("PERMISSIONS"))
        p_row = QHBoxLayout()
        self.automation_status_lbl = QLabel("")
        self.automation_status_lbl.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        p_row.addWidget(self.automation_status_lbl, 1)
        self.automation_toggle_btn = gradient_button("On" if SYSTEM_AUTOMATION_ENABLED else "Off", height=32, radius=8)
        self.automation_toggle_btn.setFixedWidth(70)
        self.automation_toggle_btn.clicked.connect(self._toggle_system_automation)
        p_row.addWidget(self.automation_toggle_btn)
        pcl.addLayout(p_row)
        pnote = QLabel("Advanced System Automation lets J.A.R.V.I.S. run shell commands, click/type in "
                       "other apps' windows, and write + test its own new tools. Each risky action still "
                       "asks you to confirm individually — this toggle just enables the capability. "
                       "Default: OFF.")
        pnote.setWordWrap(True)
        pnote.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
        pcl.addWidget(pnote)
        outer.addWidget(perm_card)
        self._refresh_automation_status()
        outer.addSpacing(16)

        # ---- Wake Word ----
        wake_card = glass_frame()
        wcl = QVBoxLayout(wake_card)
        wcl.addWidget(self._section_label('WAKE WORD ("Hey Jarvis")'))
        w_row = QHBoxLayout()
        self.wake_status_lbl = QLabel("⚪ Off")
        self.wake_status_lbl.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        w_row.addWidget(self.wake_status_lbl, 1)
        self.wake_toggle_btn = gradient_button("Turn On", height=32, radius=8)
        self.wake_toggle_btn.clicked.connect(self._toggle_wake_word)
        w_row.addWidget(self.wake_toggle_btn)
        wcl.addLayout(w_row)
        wnote = QLabel('Say "Jarvis" followed by your command — e.g. "Jarvis, take a screenshot". Uses '
                       "the same speech recognition as the Voice page (no extra account needed). Listens "
                       "in short bursts, so there's a moment of delay — this is not a dedicated low-power "
                       "wake-word engine.")
        wnote.setWordWrap(True)
        wnote.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
        wcl.addWidget(wnote)
        outer.addWidget(wake_card)
        outer.addSpacing(16)

        # ---- Voice Greeting ----
        greet_card = glass_frame()
        grl = QVBoxLayout(greet_card)
        grl.addWidget(self._section_label("STARTUP VOICE GREETING"))
        gr_row = QHBoxLayout()
        self.greet_toggle_btn = gradient_button("On" if GREETING_ENABLED else "Off", height=30, radius=8)
        self.greet_toggle_btn.setFixedWidth(60)
        self.greet_toggle_btn.clicked.connect(self._toggle_greeting)
        gr_row.addWidget(self.greet_toggle_btn)
        gr_row.addSpacing(10)
        self.greet_lang_buttons = {}
        for lang in ["english", "hindi"]:
            b = ghost_button(lang.capitalize(), height=30)
            b.clicked.connect(lambda _, l=lang: self._set_greeting_language(l))
            gr_row.addWidget(b)
            self.greet_lang_buttons[lang] = b
        gr_row.addStretch()
        grl.addLayout(gr_row)
        gnote2 = QLabel('Plays "Hello, how can I help you today?" (or Hindi equivalent) once, a couple\n'
                        "of seconds after the app opens. Also sets the voice used for spoken chat\n"
                        "replies and the conversation orb.")
        gnote2.setWordWrap(True)
        gnote2.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
        grl.addWidget(gnote2)
        outer.addWidget(greet_card)
        self._refresh_greeting_language_buttons()
        outer.addStretch()

        self.refresh_keys_list()

    def _toggle_greeting(self):
        global GREETING_ENABLED
        GREETING_ENABLED = not GREETING_ENABLED
        self.greet_toggle_btn.setText("On" if GREETING_ENABLED else "Off")
        cfg = load_config()
        cfg["greeting_enabled"] = GREETING_ENABLED
        save_config(cfg)

    def _set_greeting_language(self, lang):
        global GREETING_LANGUAGE
        GREETING_LANGUAGE = lang
        cfg = load_config()
        cfg["greeting_language"] = lang
        save_config(cfg)
        self._refresh_greeting_language_buttons()

    def _refresh_greeting_language_buttons(self):
        for lang, b in self.greet_lang_buttons.items():
            active = (lang == GREETING_LANGUAGE)
            b.setStyleSheet(f"""
                QPushButton {{ border-radius:8px; font-size:11px; font-weight:bold; padding:0 12px;
                    color:{'white' if active else '#94a3b8'};
                    background:{primary() if active else 'rgba(17,24,39,190)'};
                    border:1px solid {'transparent' if active else 'rgba(148,163,184,60)'}; }}
                QPushButton:hover {{ background:{accent() if active else 'rgba(30,41,59,220)'}; }}
            """)

    def _toggle_wake_word(self):
        wake_word_state["enabled"] = not wake_word_state["enabled"]
        if wake_word_state["enabled"]:
            self.wake_status_lbl.setText("🟢 Listening for \"Jarvis\"")
            self.wake_toggle_btn.setText("Turn Off")
            notify("Wake word enabled — say \"Jarvis\" followed by a command.", "success")
        else:
            self.wake_status_lbl.setText("⚪ Off")
            self.wake_toggle_btn.setText("Turn On")

    def _refresh_google_status(self):
        connected = google_is_connected()
        self.google_status_lbl.setText("🟢 Connected" if connected else "⚪ Not connected")
        self.google_connect_btn.setEnabled(True)

    def _refresh_automation_status(self):
        self.automation_status_lbl.setText("🟠 Enabled — risky actions still ask to confirm" if SYSTEM_AUTOMATION_ENABLED
                                            else "⚪ Disabled (safe default)")
        self.automation_toggle_btn.setText("On" if SYSTEM_AUTOMATION_ENABLED else "Off")

    def _toggle_system_automation(self):
        global SYSTEM_AUTOMATION_ENABLED
        SYSTEM_AUTOMATION_ENABLED = not SYSTEM_AUTOMATION_ENABLED
        cfg = load_config()
        cfg["system_automation_enabled"] = SYSTEM_AUTOMATION_ENABLED
        save_config(cfg)
        self._refresh_automation_status()
        notify("Advanced System Automation " + ("enabled." if SYSTEM_AUTOMATION_ENABLED else "disabled."),
               "success" if SYSTEM_AUTOMATION_ENABLED else "info")

    def _google_connect(self):
        if not GOOGLE_LIBS_AVAILABLE:
            notify("Google API libraries not installed. See Settings note / requirements.txt.", "error")
            return
        self.google_connect_btn.setEnabled(False)
        self.google_status_lbl.setText("Connecting… a browser window will open")

        def _done(result):
            self.google_connect_btn.setEnabled(True)
            self._refresh_google_status()
            notify(result, "success" if "connected" in result.lower() else "error")

        run_bg(self, google_connect, _done)

    def _google_disconnect(self):
        result = google_disconnect()
        self._refresh_google_status()
        notify(result, "info")

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#475569; font-family:Consolas; font-size:10px; font-weight:bold; background:transparent;")
        return lbl

    def _toggle_new_key_visibility(self):
        if self.new_key_entry.echoMode() == QLineEdit.EchoMode.Password:
            self.new_key_entry.setEchoMode(QLineEdit.EchoMode.Normal)
            self._eye_btn.setText("🙈")
        else:
            self.new_key_entry.setEchoMode(QLineEdit.EchoMode.Password)
            self._eye_btn.setText("👁")

    def _refresh_theme_buttons(self):
        for name, b in self.theme_btns.items():
            active = (name == current_theme_key)
            c = THEMES[name]["accent"]
            b.setStyleSheet(f"""
                QPushButton {{ text-align:left; padding-left:10px; border-radius:8px; font-size:11px;
                    font-weight:{'bold' if active else 'normal'}; color:{c if active else '#94a3b8'};
                    background:{'rgba(15,28,46,220)' if active else 'rgba(17,24,39,180)'};
                    border:1px solid {'rgba(148,163,184,60)' if not active else c}; }}
                QPushButton:hover {{ background: rgba(30,41,59,220); }}
            """)

    def apply_theme(self, name):
        global current_theme_key
        current_theme_key = name
        cfg = load_config()
        cfg["theme"] = name
        save_config(cfg)
        self._refresh_theme_buttons()
        self.mw.refresh_theme_everywhere()

    def save_name(self):
        global USER_NAME
        new_name = self.name_edit.text().strip() or "User"
        USER_NAME = new_name
        cfg = load_config()
        cfg["user_name"] = USER_NAME
        save_config(cfg)
        self.mw.sidebar.update_name()
        self.mw.home_view.set_greeting()
        self.name_status.setText("✓ Saved")
        QTimer.singleShot(1500, lambda: self.name_status.setText(""))

    def clamp_key_index(self):
        global gemini_key_index
        if not GEMINI_KEYS:
            gemini_key_index = 0
        else:
            gemini_key_index = gemini_key_index % len(GEMINI_KEYS)

    def persist_keys(self):
        cfg = load_config()
        cfg["gemini_keys"] = GEMINI_KEYS
        save_config(cfg)

    def mask_key(self, k):
        if len(k) <= 10:
            return "••••" + k[-2:] if len(k) > 2 else "••••"
        return f"{k[:6]}••••••••{k[-4:]}"

    def remove_key(self, idx):
        global GEMINI_KEYS
        if 0 <= idx < len(GEMINI_KEYS):
            GEMINI_KEYS = GEMINI_KEYS[:idx] + GEMINI_KEYS[idx + 1:]
            self.clamp_key_index()
            self.persist_keys()
            self.refresh_keys_list()

    def refresh_keys_list(self):
        while self.keys_list_lay.count():
            item = self.keys_list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not GEMINI_KEYS:
            lbl = QLabel("No keys added yet — add one below.")
            lbl.setStyleSheet("color:#64748b; font-size:10px; background:transparent;")
            self.keys_list_lay.addWidget(lbl)
        else:
            for i, k in enumerate(GEMINI_KEYS):
                row = QFrame()
                row.setStyleSheet("QFrame{background:#111827; border-radius:6px;}")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(10, 4, 6, 4)
                active_marker = "🟢 " if i == (gemini_key_index % len(GEMINI_KEYS)) else "⚪ "
                lbl = QLabel(f"{active_marker}Key {i + 1}:  {self.mask_key(k)}")
                lbl.setStyleSheet("color:#e2e8f0; font-size:11px; background:transparent;")
                rl.addWidget(lbl, 1)
                del_btn = QPushButton("✕")
                del_btn.setFixedSize(24, 22)
                del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                del_btn.setStyleSheet("QPushButton{background:#7f1d1d;color:white;border:none;border-radius:4px;font-size:10px;}"
                                       "QPushButton:hover{background:#991b1b;}")
                del_btn.clicked.connect(lambda _, idx=i: self.remove_key(idx))
                rl.addWidget(del_btn)
                self.keys_list_lay.addWidget(row)

        self.keys_count_lbl.setText(f"{len(GEMINI_KEYS)} / {MAX_KEYS} keys added   🟢 = currently active key")

    def add_key(self):
        global GEMINI_KEYS
        val = self.new_key_entry.text().strip()
        self.key_status_lbl.setText("")
        if not val:
            return
        if len(GEMINI_KEYS) >= MAX_KEYS:
            self.key_status_lbl.setText(f"⚠️ Maximum {MAX_KEYS} keys reached — remove one before adding another.")
            return
        if val in GEMINI_KEYS:
            self.key_status_lbl.setText("⚠️ That key is already in the list.")
            return
        GEMINI_KEYS = GEMINI_KEYS + [val]
        self.persist_keys()
        self.new_key_entry.clear()
        self.refresh_keys_list()

    def refresh_theme(self):
        self._refresh_theme_buttons()
        self.refresh_keys_list()
        self._refresh_greeting_language_buttons()


# ==========================================
# 19. WELCOME DIALOG (first-run setup, glass card)
# ==========================================
class WelcomeDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.mw = main_window
        self.pending_keys = []
        self._drag_pos = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(460, 660)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setStyleSheet("QFrame { background: rgba(11,15,26,238); border:1px solid rgba(148,163,184,60); border-radius:22px; }")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(44)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 190))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(28, 16, 28, 22)
        cl.setSpacing(6)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setStyleSheet("QPushButton{background:#111827;color:#e2e8f0;border-radius:15px;font-weight:bold;}"
                                 "QPushButton:hover{background:#1e293b;}")
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        cl.addLayout(close_row)

        logo_row = QHBoxLayout()
        logo_row.addStretch()
        self.reactor = ReactorWidget(110)
        logo_row.addWidget(self.reactor)
        logo_row.addStretch()
        cl.addLayout(logo_row)

        welcome_lbl = QLabel("Welcome In")
        welcome_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_lbl.setStyleSheet("color:#94a3b8; font-size:14px; background:transparent;")
        cl.addWidget(welcome_lbl)

        jarvis_lbl = QLabel("JARVIS")
        jarvis_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        jarvis_lbl.setStyleSheet("color:#f8fafc; font-family:Consolas; font-size:30px; font-weight:bold; background:transparent;")
        cl.addWidget(jarvis_lbl)

        caption = QLabel("Y O U R   P E R S O N A L   A I   A S S I S T A N T")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
        cl.addWidget(caption)
        cl.addSpacing(14)

        name_row, self.name_in = pill_input("👤", "Your Name...")
        cl.addWidget(name_row)

        key_row, self.key_in = pill_input("🔑", "Api key...", masked=True)
        eye_btn = QPushButton("👁")
        eye_btn.setFixedSize(26, 26)
        eye_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        eye_btn.setStyleSheet("QPushButton{background:transparent;color:#94a3b8;border:none;font-size:12px;}"
                               "QPushButton:hover{background:#1e293b;border-radius:13px;}")
        eye_btn.clicked.connect(lambda: self._toggle_eye(eye_btn))
        key_row.layout().addWidget(eye_btn)
        cl.addWidget(key_row)

        get_key_row = QHBoxLayout()
        get_key_row.addStretch()
        get_key_btn = QPushButton("🔗 Get API Key")
        get_key_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        get_key_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {accent()}; border: none;
                font-size: 10px; font-weight: bold; text-decoration: underline; }}
            QPushButton:hover {{ color: #ffffff; }}
        """)
        get_key_btn.clicked.connect(lambda: webbrowser.open("https://aistudio.google.com/apikey"))
        get_key_row.addWidget(get_key_btn)
        cl.addLayout(get_key_row)

        self.added_status = QLabel("You can add up to 8 keys — or just add one and start.")
        self.added_status.setStyleSheet("color:#64748b; font-size:9px; background:transparent;")
        self.added_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.added_status)

        add_keys_btn = ghost_button("+   Add Api keys", height=32)
        add_keys_btn.clicked.connect(self.add_key_to_list)
        cl.addWidget(add_keys_btn)
        cl.addSpacing(10)

        self.system_automation_check = QPushButton("☐  Enable advanced system automation")
        self.system_automation_check.setCheckable(True)
        self.system_automation_check.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.system_automation_check.setStyleSheet("""
            QPushButton { text-align:left; background:transparent; color:#94a3b8; border:none; font-size:10px; padding:2px 4px; }
            QPushButton:checked { color:#f59e0b; font-weight:bold; }
        """)
        self.system_automation_check.clicked.connect(self._toggle_automation_check)
        cl.addWidget(self.system_automation_check)
        auto_note = QLabel("Lets J.A.R.V.I.S. run shell commands, click buttons in other apps, and write "
                           "its own tools — each action is still confirmed individually. OFF is safer; "
                           "you can turn this on anytime later in Settings.")
        auto_note.setWordWrap(True)
        auto_note.setStyleSheet("color:#64748b; font-size:8px; background:transparent;")
        cl.addWidget(auto_note)
        cl.addSpacing(8)

        get_start_btn = gradient_button("▶   Get Start", height=46, radius=23)
        get_start_btn.clicked.connect(self.finish_setup)
        cl.addWidget(get_start_btn)
        cl.addSpacing(6)

        tagline = QLabel("T H I N K   •   A S K   •   A U T O M A T E   •   T O G E T H E R")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setStyleSheet("color:#475569; font-size:8px; background:transparent;")
        cl.addWidget(tagline)

    def _toggle_eye(self, eye_btn):
        if self.key_in.echoMode() == QLineEdit.EchoMode.Password:
            self.key_in.setEchoMode(QLineEdit.EchoMode.Normal)
            eye_btn.setText("🙈")
        else:
            self.key_in.setEchoMode(QLineEdit.EchoMode.Password)
            eye_btn.setText("👁")

    def add_key_to_list(self):
        val = self.key_in.text().strip()
        if not val:
            self.added_status.setText("Type a key above first.")
            self.added_status.setStyleSheet("color:#f59e0b; font-size:9px; background:transparent;")
            return
        if len(self.pending_keys) >= MAX_KEYS:
            self.added_status.setText(f"Maximum {MAX_KEYS} keys reached.")
            self.added_status.setStyleSheet("color:#ef4444; font-size:9px; background:transparent;")
            return
        if val in self.pending_keys:
            self.added_status.setText("That key is already added.")
            self.added_status.setStyleSheet("color:#f59e0b; font-size:9px; background:transparent;")
            return
        self.pending_keys.append(val)
        self.key_in.clear()
        self.added_status.setText(f"✓ {len(self.pending_keys)} / {MAX_KEYS} key(s) added.")
        self.added_status.setStyleSheet("color:#10b981; font-size:9px; background:transparent;")

    def _toggle_automation_check(self):
        checked = self.system_automation_check.isChecked()
        self.system_automation_check.setText(("☑" if checked else "☐") + "  Enable advanced system automation")

    def finish_setup(self):
        global USER_NAME, GEMINI_KEYS, gemini_key_index, SYSTEM_AUTOMATION_ENABLED
        USER_NAME = self.name_in.text().strip() or "User"
        leftover = self.key_in.text().strip()
        final_keys = list(self.pending_keys)
        if leftover and leftover not in final_keys and len(final_keys) < MAX_KEYS:
            final_keys.append(leftover)
        if final_keys:
            GEMINI_KEYS = final_keys[:MAX_KEYS]
            gemini_key_index = 0
        SYSTEM_AUTOMATION_ENABLED = self.system_automation_check.isChecked()
        cfg = load_config()
        cfg["user_name"] = USER_NAME
        if final_keys:
            cfg["gemini_keys"] = GEMINI_KEYS
        cfg["system_automation_enabled"] = SYSTEM_AUTOMATION_ENABLED
        save_config(cfg)
        self.accept()

    # allow dragging the frameless dialog by its body
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

# ==========================================
# 19b. CONVERSATION ORB (voice-to-voice, triggered by "Hey Jarvis")
# ==========================================
class ConversationOrb(QWidget):
    """A small floating always-on-top orb that appears instead of the full
    app when the wake word fires. Speaks the reply out loud and keeps
    listening for follow-up commands (no "Jarvis" prefix needed) until a
    silence timeout, then closes itself and hands the mic back to the
    background wake-word listener."""

    SILENCE_TIMEOUT_MS = 15000

    def __init__(self, main_window, initial_command: str = ""):
        super().__init__()
        self.mw = main_window
        self._closed = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(170, 190)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.width() - self.width() - 24, screen.height() - self.height() - 24)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = glass_frame(radius=20, bg="rgba(8,12,20,225)")
        outer.addWidget(card)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 6, 8, 10)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setStyleSheet("QPushButton{background:#111827;color:#e2e8f0;border-radius:10px;font-size:9px;}"
                                 "QPushButton:hover{background:#1e293b;}")
        close_btn.clicked.connect(self.close_orb)
        close_row.addWidget(close_btn)
        cl.addLayout(close_row)

        reactor_row = QHBoxLayout()
        reactor_row.addStretch()
        self.reactor = ReactorWidget(100)
        reactor_row.addWidget(self.reactor)
        reactor_row.addStretch()
        cl.addLayout(reactor_row)

        self.status_lbl = QLabel("Listening...")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet(f"color:{accent()}; font-size:11px; font-weight:bold; background:transparent;")
        cl.addWidget(self.status_lbl)

        self._silence_timer = QTimer(self)
        self._silence_timer.setSingleShot(True)
        self._silence_timer.timeout.connect(self.close_orb)

        wake_word_state["suspended"] = True
        self.show()
        self.raise_()

        if initial_command:
            self._handle_utterance(initial_command)
        else:
            self._start_listen_cycle()

    def _start_listen_cycle(self):
        if self._closed:
            return
        self.status_lbl.setText("Listening...")
        self._silence_timer.start(self.SILENCE_TIMEOUT_MS)
        threading.Thread(target=self._listen_once, daemon=True).start()

    def _listen_once(self):
        if not sr:
            QTimer.singleShot(0, self.close_orb)
            return
        r = sr.Recognizer()
        heard = ""
        try:
            with sr.Microphone() as source:
                r.adjust_for_ambient_noise(source, duration=0.3)
                audio = r.listen(source, timeout=4, phrase_time_limit=8)
            heard = r.recognize_google(audio).strip()
        except Exception:
            heard = ""
        if self._closed:
            return
        if heard:
            QTimer.singleShot(0, lambda: self._handle_utterance(heard))
        else:
            QTimer.singleShot(200, self._start_listen_cycle)

    def _handle_utterance(self, text: str):
        if self._closed:
            return
        self._silence_timer.stop()
        self.status_lbl.setText("Thinking...")
        threading.Thread(target=self._process, args=(text,), daemon=True).start()

    def _process(self, text: str):
        try:
            cs = self.mw.chat_view.current_session_id
        except Exception:
            cs = None
        reply = process_voice_command(text, session_id=cs)
        try:
            if cs:
                db_save_message(cs, "user", text, "")
                db_save_message(cs, "jarvis", reply, "")
        except Exception:
            pass
        if self._closed:
            return
        QTimer.singleShot(0, lambda: self._speak_and_continue(reply))

    def _speak_and_continue(self, reply: str):
        if self._closed:
            return
        self.status_lbl.setText("Speaking...")

        def _tts_worker():
            speak_text_sync(reply)
            if not self._closed:
                QTimer.singleShot(0, self._start_listen_cycle)

        threading.Thread(target=_tts_worker, daemon=True).start()

    def close_orb(self):
        if self._closed:
            return
        self._closed = True
        wake_word_state["suspended"] = False
        self.hide()
        self.deleteLater()

# ==========================================
# 19c. CODE APPROVAL DIALOG (self-writing tools safety gate)
# ==========================================
class HealthCheckDialog(QDialog):
    """One-click environment check: shows what works, what doesn't and exactly how to fix it."""
    row_ready = pyqtSignal(str, str, str, str)
    all_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("J.A.R.V.I.S. Health Check")
        self.resize(720, 600)
        self.setStyleSheet("QDialog { background: #0a0f19; }")
        self._rows = []
        lay = QVBoxLayout(self)
        title = QLabel("🩺 System Health Check")
        title.setStyleSheet(f"color:{accent()}; font-size:16px; font-weight:bold; background:transparent;")
        lay.addWidget(title)
        self.sub = QLabel("Checking… this takes a few seconds.")
        self.sub.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        lay.addWidget(self.sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        self.list_lay = QVBoxLayout(host)
        self.list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.list_lay.setSpacing(8)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)

        btns = QHBoxLayout()
        self.copy_btn = ghost_button("📋 Copy report", height=36)
        self.copy_btn.clicked.connect(self._copy)
        self.copy_btn.setEnabled(False)
        btns.addWidget(self.copy_btn)
        self.rerun_btn = ghost_button("↻ Run again", height=36)
        self.rerun_btn.clicked.connect(self._start)
        self.rerun_btn.setEnabled(False)
        btns.addWidget(self.rerun_btn)
        btns.addStretch()
        close_btn = gradient_button("Close", height=36, radius=8)
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        lay.addLayout(btns)

        self.row_ready.connect(self._add_row)
        self.all_done.connect(self._finished)
        self._start()

    def _start(self):
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._rows = []
        self.sub.setText("Checking… this takes a few seconds.")
        self.copy_btn.setEnabled(False)
        self.rerun_btn.setEnabled(False)

        def _run():
            try:
                run_health_checks(lambda l, st, d, f: self.row_ready.emit(l, st, d, f))
            except Exception as e:
                self.row_ready.emit("Health check", "fail", str(e)[:160], "")
            self.all_done.emit()
        threading.Thread(target=_run, daemon=True).start()

    def _add_row(self, label, status, detail, fix):
        self._rows.append((label, status, detail, fix))
        icon, col = {"ok": ("✅", "#22c55e"), "warn": ("⚠️", "#f59e0b"), "fail": ("❌", "#ef4444")}.get(status, ("•", "#94a3b8"))
        card = QFrame()
        card.setObjectName("hcRow")
        card.setStyleSheet(f"QFrame#hcRow {{ background: rgba(17,24,39,200); border: 1px solid {col}55; border-radius: 10px; }}")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        head = QLabel(f"{icon}  <b>{label}</b> — {detail}")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        head.setStyleSheet("color:#e2e8f0; font-size:12px; background:transparent; border:none;")
        v.addWidget(head)
        if fix:
            f = QLabel("Fix: " + fix)
            f.setWordWrap(True)
            f.setStyleSheet(f"color:{col}; font-size:11px; background:transparent; border:none;")
            v.addWidget(f)
        self.list_lay.addWidget(card)

    def _finished(self):
        bad = sum(1 for r in self._rows if r[1] == "fail")
        warn = sum(1 for r in self._rows if r[1] == "warn")
        if bad:
            self.sub.setText(f"{bad} problem(s) need fixing, {warn} warning(s). Fix the red items first.")
        elif warn:
            self.sub.setText(f"No blocking problems — {warn} optional warning(s).")
        else:
            self.sub.setText("Everything looks healthy. ✅")
        self.copy_btn.setEnabled(True)
        self.rerun_btn.setEnabled(True)

    def _copy(self):
        txt = "J.A.R.V.I.S. health report\n" + "\n".join(
            f"[{st.upper()}] {l}: {d}" + (f"  -> {f}" if f else "") for l, st, d, f in self._rows)
        QApplication.clipboard().setText(txt)
        notify("Health report copied — you can paste it to get help.", "info")

class CodeApprovalDialog(QDialog):
    """Shown whenever the AI proposes writing a brand-new tool. The code is
    NEVER executed without the user explicitly clicking Approve here —
    this is the safety gate for Feature 4 (self-evolving tools)."""

    def __init__(self, parent, tool_name: str, description: str, code: str):
        super().__init__(parent)
        self.setWindowTitle("New Tool Proposed by J.A.R.V.I.S.")
        self.resize(640, 560)
        self.setStyleSheet(f"QDialog {{ background: #0a0f19; }}")
        self._approved = False
        self._code = code

        lay = QVBoxLayout(self)
        header = QLabel(f"⚠️ J.A.R.V.I.S. wants to create a new tool: '{tool_name}'")
        header.setWordWrap(True)
        header.setStyleSheet(f"color:{accent()}; font-size:14px; font-weight:bold; background:transparent;")
        lay.addWidget(header)

        desc_lbl = QLabel(description)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        lay.addWidget(desc_lbl)

        warn_lbl = QLabel("Review the generated Python code below. It will only run on your machine "
                          "(sandboxed test first, 12s timeout) if you click Approve. You can edit it "
                          "before approving if something looks wrong.")
        warn_lbl.setWordWrap(True)
        warn_lbl.setStyleSheet("color:#f59e0b; font-size:10px; background:transparent;")
        lay.addWidget(warn_lbl)

        self.code_box = QTextEdit()
        self.code_box.setPlainText(code)
        self.code_box.setStyleSheet("QTextEdit{background:#050811;color:#a5f3fc;font-family:Consolas;"
                                     "font-size:11px;border:1px solid #1e293b;border-radius:8px;padding:8px;}")
        lay.addWidget(self.code_box, 1)

        btn_row = QHBoxLayout()
        reject_btn = ghost_button("✕ Reject", height=38)
        reject_btn.clicked.connect(self._reject)
        btn_row.addWidget(reject_btn)
        btn_row.addStretch()
        approve_btn = gradient_button("✓ Approve & Test", height=38, radius=8)
        approve_btn.clicked.connect(self._approve)
        btn_row.addWidget(approve_btn)
        lay.addLayout(btn_row)

    def _approve(self):
        self._approved = True
        self._code = self.code_box.toPlainText()
        self.accept()

    def _reject(self):
        self._approved = False
        self.reject()

# ==========================================
# 20. MAIN WINDOW
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Fit the window to THIS screen (laptops are often 1366x768 / scaled) —
        # a fixed 1440x900 pushed the bottom (chat input, Home ask box) under the taskbar.
        _avail = QApplication.primaryScreen().availableGeometry()
        _w = min(1440, _avail.width() - 24)
        _h = min(900, _avail.height() - 24)
        self.setMinimumSize(min(1000, _w), min(600, _h))
        self.resize(_w, _h)
        self.move(_avail.x() + (_avail.width() - _w) // 2, _avail.y() + (_avail.height() - _h) // 2)

        icon_path = ensure_app_icon()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))
        self._setup_tray_icon(icon_path)

        central = QWidget()
        central.setObjectName("root")
        central.setStyleSheet("""
            #root { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #05070c, stop:1 #0a1420);
                    border-radius: 18px; }
        """)
        self.setCentralWidget(central)

        # Frameless windows have no native resize handles — a QSizeGrip in
        # the bottom-right corner is Qt's own safe, built-in way to resize
        # one (it handles all the mouse-drag math itself; nothing custom).
        self.size_grip = QSizeGrip(central)
        self.size_grip.setStyleSheet("background: transparent;")
        self.size_grip.setFixedSize(16, 16)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)

        body_wrap = QWidget()
        body = QHBoxLayout(body_wrap)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = Sidebar(self.select_view)
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        outer.addWidget(body_wrap, 1)

        # ChatView must be created before HomeView: HomeView's initial chip
        # selection synchronously calls self.mw.chat_view.set_mode_badge(),
        # so chat_view has to exist first (this ordering caused the
        # AttributeError: 'MainWindow' object has no attribute 'chat_view').
        self.chat_view = ChatView(self)
        self.home_view = HomeView(self)
        self.settings_view = SettingsView(self)

        self.tasks_view = TasksView(self)
        self.voice_view = VoiceView(self)
        self.apps_view = AppsControlView(self)
        self.automation_view = AutomationView(self)
        self.files_view = FilesView(self)
        self.research_view = ResearchView(self)
        self.tools_view = ToolsView(self)
        self.views = {"home": self.home_view, "chat": self.chat_view, "settings": self.settings_view,
                      "tasks": self.tasks_view, "voice": self.voice_view, "apps": self.apps_view,
                      "automation": self.automation_view, "files": self.files_view,
                      "research": self.research_view, "tools": self.tools_view}

        self.stack_index = {}
        for icon, label, key in NAV_ITEMS:
            self.stack.addWidget(self.views[key])
            self.stack_index[key] = self.stack.count() - 1

        self.select_view("home")
        self.chat_view.load_sessions_from_db()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self.home_view.refresh_status_bars)
        self._status_timer.start(4000)
        self.home_view.refresh_status_bars()
        self.home_view.refresh()

        bridge.hotkey_toggle.connect(self.toggle_visibility)
        bridge.toast.connect(self.show_toast)
        bridge.reminder_fired.connect(self._on_reminder_fired)
        bridge.wake_word_detected.connect(self._on_wake_word)
        bridge.confirmation_requested.connect(self._on_confirmation_requested)
        bridge.code_approval_requested.connect(self._on_code_approval_requested)
        self._register_hotkey()

        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.timeout.connect(self._check_background_threads)
        self._watchdog_timer.start(30000)

        self._alerted_event_ids = set()
        self._calendar_alert_timer = QTimer(self)
        self._calendar_alert_timer.timeout.connect(self._check_calendar_alerts)
        self._calendar_alert_timer.start(300000)  # every 5 minutes

        if IS_FIRST_RUN:
            QTimer.singleShot(300, self.show_welcome)
        elif GREETING_ENABLED:
            QTimer.singleShot(1200, self._play_startup_greeting)

        self.size_grip.move(self.width() - 18, self.height() - 18)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self.size_grip.move(self.width() - 18, self.height() - 18)
        except Exception:
            pass

    def select_view(self, key):
        idx = self.stack_index.get(key, 0)
        self.stack.setCurrentIndex(idx)
        self._fade_in_current_page()
        self.sidebar.set_active(key)
        self.sidebar._current_key = key
        if key == "home":
            self.home_view.refresh()
        elif key == "tasks":
            self.tasks_view.refresh()
        elif key == "files":
            self.files_view.refresh()

    def _fade_in_current_page(self):
        """Soft fade-in whenever a page becomes visible, instead of the
        instant snap-cut that made switching pages feel flat/static."""
        page = self.stack.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", page)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda p=page: p.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        page._fade_anim = anim  # keep a reference alive until it finishes

    def refresh_theme_everywhere(self):
        self.titlebar.refresh_theme()
        self.sidebar.refresh_theme()
        self.home_view.refresh_theme()
        self.chat_view.refresh_theme()
        self.settings_view.refresh_theme()
        self.tasks_view.refresh_theme()
        self.voice_view.refresh_theme()
        self.apps_view.refresh_theme()
        self.automation_view.refresh_theme()
        self.files_view.refresh_theme()
        self.research_view.refresh_theme()
        self.tools_view.refresh_theme()

    def show_welcome(self):
        dlg = WelcomeDialog(self)
        dlg.exec()
        self.sidebar.update_name()
        self.home_view.set_greeting()
        self.settings_view.name_edit.setText(USER_NAME)
        self.settings_view.refresh_keys_list()

    def _play_startup_greeting(self):
        p = persona()
        template = p["greeting_hi"] if GREETING_LANGUAGE == "hindi" else p["greeting_en"]
        text = template.format(name=USER_NAME)
        threading.Thread(target=speak_text_sync, args=(text,), daemon=True).start()

    # ---------- persona engine ----------
    def set_persona(self, name: str, announce: bool = True):
        """Switch the active persona everywhere: colors, reactor shape,
        names, voice. Backend/abilities are identical for all personas."""
        if not switch_persona(name):
            return
        self._color_wash()
        self.apply_persona_everywhere()
        self._fade_in_current_page()
        if announce:
            p = persona()
            template = p["greeting_hi"] if GREETING_LANGUAGE == "hindi" else p["greeting_en"]
            threading.Thread(target=speak_text_sync, args=(template.format(name=USER_NAME),),
                             daemon=True).start()

    def apply_persona_everywhere(self):
        self.setWindowTitle(persona()["full_name"])
        self.titlebar.apply_persona()
        self.sidebar.apply_persona()
        self.home_view.reactor.apply_persona()
        self.home_view.persona_capsules.refresh()
        self.settings_view.persona_capsules.refresh()
        self.refresh_theme_everywhere()
        if getattr(self, "tray_icon", None):
            self.tray_icon.setToolTip(persona()["full_name"])

    def _color_wash(self):
        """Brief full-window wash of the NEW persona's color that fades out —
        the smooth transition between personas."""
        central = self.centralWidget()
        wash = QFrame(central)
        wash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        wash.setGeometry(0, 0, central.width(), central.height())
        c = QColor(accent())
        wash.setStyleSheet(f"background: rgba({c.red()},{c.green()},{c.blue()},110); border-radius:18px;")
        eff = QGraphicsOpacityEffect(wash)
        wash.setGraphicsEffect(eff)
        wash.show()
        wash.raise_()
        anim = QPropertyAnimation(eff, b"opacity", wash)
        anim.setDuration(550)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(wash.deleteLater)
        anim.start()
        wash._anim = anim

    def toggle_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def show_toast(self, message: str, kind: str = "info"):
        Toast(self, message, kind)

    def _setup_tray_icon(self, icon_path: str):
        self.tray_icon = None
        if not icon_path or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_icon = QSystemTrayIcon(QIcon(icon_path), self)
        self.tray_icon.setToolTip("J.A.R.V.I.S.")
        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{ background:#090e17; color:#e2e8f0; border:1px solid {accent()}; border-radius:8px; padding:4px; }}
            QMenu::item {{ padding:7px 18px; border-radius:6px; }}
            QMenu::item:selected {{ background:{primary()}; }}
        """)
        show_action = menu.addAction("⚡ Show J.A.R.V.I.S.")
        show_action.triggered.connect(self.toggle_visibility)
        menu.addSeparator()
        quit_action = menu.addAction("✕ Quit")
        quit_action.triggered.connect(QApplication.instance().quit)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_visibility()

    def close_to_tray(self):
        """The titlebar's X button minimizes to the tray instead of quitting
        outright — use the tray icon's right-click 'Quit' to actually exit."""
        if getattr(self, "tray_icon", None) and self.tray_icon.isSystemTrayAvailable():
            self.hide()
            if not getattr(self, "_tray_notice_shown", False):
                self._tray_notice_shown = True
                self.tray_icon.showMessage("J.A.R.V.I.S. is still running",
                                            "Minimized to the tray. Right-click the tray icon to quit.",
                                            QSystemTrayIcon.MessageIcon.Information, 5000)
        else:
            QApplication.instance().quit()

    def _on_reminder_fired(self, label: str):
        self.show_toast(f"🔔 Reminder: {label}", "success")
        if getattr(self, "tray_icon", None) and self.tray_icon.isSystemTrayAvailable():
            self.tray_icon.showMessage("J.A.R.V.I.S. Reminder", label, QSystemTrayIcon.MessageIcon.Information, 6000)

    def _on_wake_word(self, persona_name: str, command: str):
        # saying "Hey Friday" switches to Friday first, then opens the orb as her
        if persona_name != CURRENT_PERSONA:
            self.set_persona(persona_name, announce=False)
        if getattr(self, "_orb", None):
            try:
                self._orb.close_orb()
            except Exception:
                pass
        self._orb = ConversationOrb(self, initial_command=command)

    def _on_confirmation_requested(self, title, message, result_holder, event):
        try:
            box = QMessageBox(self)
            box.setWindowTitle(title)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(message)
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            choice = box.exec()
            result_holder["confirmed"] = (choice == QMessageBox.StandardButton.Yes)
        except Exception as e:
            log_error(f"Confirmation dialog error: {e}")
            result_holder["confirmed"] = False
        finally:
            event.set()

    def _on_code_approval_requested(self, tool_name, description, code, result_holder, event):
        try:
            dlg = CodeApprovalDialog(self, tool_name, description, code)
            outcome = dlg.exec()
            result_holder["approved"] = bool(dlg._approved and outcome == QDialog.DialogCode.Accepted)
            result_holder["code"] = dlg._code
        except Exception as e:
            log_error(f"Code approval dialog error: {e}")
            result_holder["approved"] = False
        finally:
            event.set()

    def _register_hotkey(self):
        def _register():
            try:
                keyboard.add_hotkey('ctrl+space', lambda: bridge.hotkey_toggle.emit())
                keyboard.add_hotkey('ctrl+alt+j', lambda: bridge.hotkey_toggle.emit())
            except Exception as e:
                notify(f"Global hotkey (Ctrl+Space) couldn't register: {e}. "
                       f"On Windows this often needs the app run as Administrator.", "error")
        threading.Thread(target=_register, daemon=True).start()

    def _check_background_threads(self):
        """Watchdog: every 30s, checks perpetual background threads (like
        the wake-word listener) and restarts any that died unexpectedly —
        the loop's own try/except already makes this extremely rare, but
        this is a safety net rather than relying on that alone."""
        for name, (t, target) in list(background_threads.items()):
            if not t.is_alive():
                notify(f"Background task '{name}' stopped unexpectedly — restarting it.", "error")
                start_managed_thread(name, target)

    def _check_calendar_alerts(self):
        """Every 5 minutes, checks (on a background thread) for Google
        Calendar events starting within 15 minutes and alerts once per
        event via toast + tray notification. No-ops silently if Google
        isn't connected."""
        def _bg():
            events = get_upcoming_events_structured(15)
            for eid, summary, start_raw in events:
                if not eid or eid in self._alerted_event_ids:
                    continue
                self._alerted_event_ids.add(eid)
                when = format_event_time(start_raw)
                message = f"📅 Upcoming: {summary} at {when}"
                bridge.toast.emit(message, "info")
                if getattr(self, "tray_icon", None) and self.tray_icon.isSystemTrayAvailable():
                    self.tray_icon.showMessage("J.A.R.V.I.S. — Upcoming Event", f"{summary} at {when}",
                                                QSystemTrayIcon.MessageIcon.Information, 8000)
        threading.Thread(target=_bg, daemon=True).start()

# ==========================================
# 21. ENTRY POINT
# ==========================================
def _global_exception_hook(exc_type, exc_value, exc_tb):
    """Catches anything that would otherwise crash the app silently —
    crucial once packaged as a --windowed .exe with no visible console.
    Logs the full traceback to jarvis.log and shows a toast if the GUI is
    already up, instead of the app just vanishing with no explanation."""
    import traceback
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.error(f"UNCAUGHT EXCEPTION:\n{tb_text}")
    print(tb_text)
    try:
        bridge.toast.emit(f"Something went wrong: {exc_value}", "error")
    except Exception:
        pass

def _thread_exception_hook(args):
    """Same idea as _global_exception_hook, but for exceptions raised on
    any OTHER thread — sys.excepthook only ever catches the main thread's.
    Background threads (TTS playback, tool execution, voice capture, etc.)
    can otherwise die silently with nothing visible anywhere."""
    import traceback
    tb_text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    thread_name = getattr(args.thread, "name", "unknown")
    logger.error(f"THREAD EXCEPTION in '{thread_name}':\n{tb_text}")
    print(tb_text)
    try:
        bridge.toast.emit(f"A background task hit an error and stopped ({thread_name}).", "error")
    except Exception:
        pass

def main():
    sys.excepthook = _global_exception_hook
    threading.excepthook = _thread_exception_hook
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    log_info("J.A.R.V.I.S. started.")
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
