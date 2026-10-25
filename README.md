# ⚡ J.A.R.V.I.S. — Tri-Persona AI Desktop Assistant

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PyQt6](https://img.shields.io/badge/GUI-PyQt6%20Glassmorphism-41CD52?style=for-the-badge&logo=qt&logoColor=white)
![Gemini](https://img.shields.io/badge/AI-Google%20Gemini%202.5-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white)
![Playwright](https://img.shields.io/badge/Browser-Playwright%20CDP-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-00f2fe?style=for-the-badge)

**A frameless, glassmorphic Windows AI desktop assistant featuring three switchable AI personas (`JARVIS`, `FRIDAY`, `ELEVEN`), voice wake-word & floating conversation orb, multi-step agentic tool calling, persistent SQLite memory, Google Workspace integration, Playwright CDP web automation, and guarded Windows desktop control.**

</div>

---

## ✨ Key Highlights

- **🎭 Tri-Persona Architecture**: Switch seamlessly between **J.A.R.V.I.S.**, **F.R.I.D.A.Y.**, and **E.L.E.V.E.N.**—each with its own wake word, distinct personality prompt, UI badge, and natural neural voice (English & Hindi via `edge-tts`).
- **🖥️ 10 Live Glassmorphic Dashboard Views**: Built on PyQt6 with real `QGraphicsBlurEffect` reactor glow, animated progress bars, 3 HUD color themes (**Arc Cyan**, **Matrix Emerald**, **Stark Crimson**), system tray integration, and toast notifications.
- **🔑 Up to 8 Gemini API Keys (BYOK) with Instant Failover**: Configure 1–8 Gemini API keys in Settings or via `GEMINI_API_KEYS`. Automatically rotates keys on quota/rate-limit errors with zero interruption.
- **🧠 Multi-Step Agentic Dispatch & Persistent Memory**: Chains multiple tool calls per request, stores chat sessions and reminders in `jarvis_memory.db` (SQLite), and injects long-term user facts automatically across conversations.
- **🌐 Playwright Chrome CDP & Windows UIAutomation**: Controls a dedicated, isolated Chrome profile (`jarvis_chrome_profile` over CDP port `9222`) without touching your personal browser data, alongside native Windows UI element inspection and guarded system control.
- **☁️ Google Workspace Integration**: Native OAuth 2.0 support for **Gmail** (read/send), **Google Drive** (list/upload), **Google Docs** (create/write), **Google Calendar** (list/create + 15-minute proactive event alerts), and **YouTube Data API v3**.

---

## 🎭 Tri-Persona System

| Persona | Full Name | Wake Word | English Voice (`edge-tts`) | Hindi Voice (`edge-tts`) |
| :--- | :--- | :--- | :--- | :--- |
| **JARVIS** | Just A Rather Very Intelligent System | `"jarvis"` | `en-US-ChristopherNeural` | `hi-IN-MadhurNeural` |
| **FRIDAY** | Female Replacement Intelligent Digital Assistant Youth | `"friday"` | `en-IN-NeerjaExpressiveNeural` | `hi-IN-SwaraNeural` |
| **ELEVEN** | Enhanced Logical Execution & Virtual Neural-network | `"eleven"` | `en-US-AriaNeural` | `hi-IN-SwaraNeural` |

When the background wake-word listener is enabled in **Settings**, saying `"Jarvis, ..."`, `"Friday, ..."`, or `"Eleven, ..."` launches the always-on-top **Floating Conversation Orb** for hands-free voice-to-voice interaction.

---

## 🧭 10 Live Dashboard Pages

| Page | Icon | Core Capabilities |
| :--- | :---: | :--- |
| **Home** | 🏠 | Animated Arc Reactor, time-aware greeting, live CPU/RAM/Disk/Battery/Network telemetry, Quick Actions, and recent activity feed |
| **Chat** | 💬 | Multi-session chat, 5 AI Modes, drag-and-drop image/PDF/TXT analysis, live screen sight (`"look at my screen"`), and runnable `CodeCard` blocks |
| **Voice** | 🎙️ | Dedicated push-to-talk speech transcription with editable preview before dispatching to Chat |
| **Tasks** | 🗒️ | Persistent SQLite countdown timers & reminders with sound + system tray alerts |
| **Automation** | ⚡ | Natural-language multi-tool command runner and instant hardware action tiles |
| **Apps & Control** | 🧩 | One-tap launchers for YouTube, WhatsApp, Gmail, GitHub, Screenshot, QR Generator, Volume, Brightness, and Google Workspace |
| **Files & Memory** | 📁 | Search, pin, rename, reopen, or delete chat sessions; view and manage long-term user memory facts |
| **Research** | 🔎 | Direct Wikipedia encyclopedia search and clean webpage text scraper |
| **Tools** | 🛠️ | Live Binance Crypto Ticker, Frankfurter Forex Currency Converter, Forex Lot Size Calculator, and QR Code Generator |
| **Settings** | ⚙️ | HUD Theme switcher, Persona & Greeting language selector (English/Hindi), 8-key Gemini API manager, Google OAuth, and Wake-Word toggle |

### 🧠 5 Specialized AI Modes in Chat
1. **💬 General** — Fast, concise everyday assistance with long-term memory recall (`remember_fact`) and hardware status.
2. **📖 Research** — Fact-checked synthesis powered by `search_wikipedia` and `scrape_webpage`.
3. **</> Coder** — Generates clean code inside interactive `CodeCard` blocks with one-click **Copy** and **▶ Run** execution.
4. **🎨 Creative** — Brainstorming, storytelling, and copywriting mode.
5. **⚡ Automation** — Full access to all 25+ desktop, web, finance, media, and Google Workspace tools.

---

## 🚀 Getting Started

### 1. Prerequisites
- **OS**: Windows 10 or Windows 11 (64-bit)
- **Python**: Python **3.12** recommended
- **Google Chrome**: Installed on Windows (used automatically by Playwright CDP—no separate browser download required)

### 2. Installation

Clone the repository and install the dependencies:

```bash
git clone https://github.com/princeraj-in/JARVIS.git
cd JARVIS
pip install -r requirements.txt
```

### 3. Configure Your Gemini API Key (BYOK)
1. Get a free or paid Gemini API key from [Google AI Studio](https://aistudio.google.com/).
2. Run J.A.R.V.I.S.:
   ```bash
   python jarvis.py
   ```
3. On first launch, enter your name and paste up to **8 Gemini API keys** in the **Welcome Dialog** (or add them anytime under **Settings → Gemini API Keys**).
   - *Optional*: You can also set the `GEMINI_API_KEYS` environment variable as a comma-separated list (`key1,key2,key3`).

---

## ☁️ Optional: Google Workspace OAuth Setup

To enable **Gmail**, **Google Drive**, **Google Docs**, **Google Calendar**, and **YouTube Data API v3** tools:

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Go to **APIs & Services → Library** and enable:
   - **Gmail API**
   - **Google Drive API**
   - **Google Docs API**
   - **Google Calendar API**
   - **YouTube Data API v3**
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
4. Choose **Desktop app** as the Application type and download the JSON file.
5. Rename the downloaded file to `credentials.json` and place it in the root folder next to `jarvis.py` (or next to `JARVIS.exe`).
6. Open J.A.R.V.I.S. → **Settings → Google Workspace → Connect Google Account** and approve access in your browser. A local `token.json` will be saved automatically.

> 🔒 **Security Note**: `credentials.json`, `token.json`, and `jarvis_config.json` are excluded via `.gitignore` and never leave your machine.

---

## 📦 Building a Standalone Windows `.exe`

Package J.A.R.V.I.S. into a single windowed executable (`dist\JARVIS.exe`) with no console window required:

```cmd
build_exe.bat
```

What `build_exe.bat` does:
1. Installs/updates all packages from `requirements.txt`.
2. Generates the multi-size Arc Reactor icon (`jarvis_icon.ico`) if not already present.
3. Runs **PyInstaller** (`--noconfirm --onefile --windowed`) bundling `PyQt6`, `edge_tts`, `pywhatkit`, and `playwright`.
4. Outputs `dist\JARVIS.exe`. When you run `JARVIS.exe`, your local files (`jarvis_config.json`, `jarvis_memory.db`, `jarvis.log`, `token.json`) are created right next to the `.exe`.

---

## ⌨️ Keyboard Shortcuts & Voice Commands

| Shortcut / Trigger | Action |
| :--- | :--- |
| `Ctrl + Space` | Global hotkey to show / hide the J.A.R.V.I.S. window from anywhere |
| `Ctrl + Alt + J` | Secondary global hotkey to toggle window visibility |
| `Enter` | Send message in Chat input |
| `Shift + Enter` | Insert newline in Chat input |
| `"Jarvis, <command>"` | Wake-word trigger for J.A.R.V.I.S. persona (opens Floating Conversation Orb) |
| `"Friday, <command>"` | Wake-word trigger for F.R.I.D.A.Y. persona |
| `"Eleven, <command>"` | Wake-word trigger for E.L.E.V.E.N. persona |
| `"Look at my screen"` | Captures live desktop screenshot and analyzes it with Gemini Vision |

---

## 🛠️ Troubleshooting

- **Global Hotkey (`Ctrl+Space`) Not Registering on Windows**:
  The Python `keyboard` library may require Administrator privileges on some Windows setups. Right-click your terminal or `JARVIS.exe` and select **Run as administrator**.
- **`PyAudio` Installation Error**:
  On Python 3.12+ (Windows), `pip install PyAudio` installs prebuilt wheels automatically. If it fails on an older Python build, upgrade `pip` first: `python -m pip install --upgrade pip setuptools wheel`.
- **Window Close (`✕`) Button Behavior**:
  Clicking `✕` on the custom title bar minimizes J.A.R.V.I.S. to the **Windows System Tray** so background reminders, calendar alerts, and wake-word listening stay active. To exit completely, right-click the tray icon and click **✕ Quit**.

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
