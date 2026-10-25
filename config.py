import os
from dotenv import load_dotenv

load_dotenv()

# Brain: a local model served by Ollama on this PC
# 127.0.0.1, never "localhost": Windows tries IPv6 (::1) first, Ollama only listens on IPv4, and every
# reconnect after a >5 s pause (i.e. every voice turn) waited ~2 s for that to fail (measured).
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").replace("//localhost", "//127.0.0.1")
MODEL = os.getenv("JARVIS_MODEL", "qwen3:8b")
CONTEXT_TOKENS = int(os.getenv("CONTEXT_TOKENS", "4096"))  # smaller = less VRAM
# Ollama memory-maps the model file into system RAM even though it runs on the GPU (8.2 GB working set on a
# 16 GB PC at 98% full). Off = weights read once onto the GPU. Measured in the commit that added this.
USE_MMAP = os.getenv("USE_MMAP", "0") == "1"
USER_NAME = os.getenv("JARVIS_USER", "friend")   # your name: she calls you by it
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Keena")   # her name: spoken, shown, and the wake word (voice/stt.py)
OFFER_REMINDERS = os.getenv("OFFER_REMINDERS", "1") != "0"   # plans with times -> "Shall I remind you?" (set only on yes)
LOG_CONVERSATIONS =os.getenv("LOG_CONVERSATIONS", "1") != "0"   # every turn saved locally in data/jarvis.db

# Audio
SAMPLE_RATE = 16000          # openWakeWord + Whisper both expect 16 kHz
FRAME = 1280                 # 80 ms chunks, the size openWakeWord likes
WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.35"))
# How Jarvis listens for its name:
#   model   = openWakeWord "hey jarvis" model only (light, but weak with Indian accents)
#   whisper = Whisper listens for the word "Jarvis" in anything you say (accent-proof, more CPU)
#   both    = either one wakes it (default)
# openWakeWord only knows "hey jarvis", so since the rename to Keena it is Whisper by default
WAKE_MODE = os.getenv("WAKE_MODE", "whisper").strip().lower()
FOLLOW_UP_SECONDS = float(os.getenv("FOLLOW_UP_SECONDS", "6"))  # keep listening after a reply, no wake word needed
SILENCE_RMS = int(os.getenv("SILENCE_RMS", "500"))
_mic = os.getenv("MIC_DEVICE", "").strip()
MIC_DEVICE = int(_mic) if _mic.isdigit() else (_mic or None)  # None = Windows default mic
SILENCE_SECONDS = float(os.getenv("SILENCE_SECONDS", "0.7"))  # stop recording after this much quiet
MAX_RECORD_SECONDS = 30      # hard cap on one command (long enough to dictate an email)
WAIT_FOR_SPEECH_SECONDS = 5  # give up if you say nothing after the wake word

# Speech
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
VOICE = os.getenv("JARVIS_VOICE", "en-US-AvaNeural")   # Keena: warm, natural (he picked it from 13 samples)
VOICE_RATE = os.getenv("VOICE_RATE", "+0%")   # Ryan ran at +8%; Ava sounds best at her own pace
# Tamil / Tanglish understanding (voice/language.py): when Whisper is unsure, Gemini turns his speech into English
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TAMIL_MODEL = os.getenv("TAMIL_MODEL", "gemini-3.1-flash-lite")   # understands his Tamil (~1.5-2 s); 3.5-flash-lite was 3x slower

# Live feedback
HUD = os.getenv("HUD", "1") != "0"                    # on-screen orb (needs: pip install PySide6)
HUD_POSITION = os.getenv("HUD_POSITION", "bottom-right").strip().lower()   # bottom-right / bottom-left / top-right / top-left
try:
    HUD_SCALE = min(2.0, max(0.6, float(os.getenv("HUD_SCALE", "1.0"))))  # 0.8 = smaller corner HUD, 1.2 = bigger
except ValueError:
    HUD_SCALE = 1.0
FILLERS = os.getenv("FILLERS", "1") != "0"            # "let me think..." before slower AI answers
# Words appear while you talk. Needs Whisper on the GPU to be fast enough ("auto" = on when WHISPER_DEVICE=cuda)
_live = os.getenv("LIVE_TRANSCRIPT", "auto").lower()
LIVE_TRANSCRIPT = (os.getenv("WHISPER_DEVICE", "cpu") == "cuda") if _live == "auto" else _live in ("1", "true", "on")

SCREENSHOT_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "Jarvis")

# Jarvis Remote: control Jarvis from his phone (a private web page). Reachable over Tailscale (anywhere) and, with
# REMOTE_LAN=1, the home Wi-Fi; every request needs the secret key from data/remote_key.txt (python -m remote.pair).
REMOTE = os.getenv("REMOTE", "1") != "0"
REMOTE_PORT = int(os.getenv("REMOTE_PORT", "8765"))
REMOTE_HTTPS_PORT = int(os.getenv("REMOTE_HTTPS_PORT", "8766"))   # secure page: the phone mic needs it (0 = off)
REMOTE_LAN = os.getenv("REMOTE_LAN", "1") != "0"
REMOTE_SPEAK = os.getenv("REMOTE_SPEAK", "0") == "1"   # also say phone replies out loud on the PC (phone can toggle)

# Browser control: web pages open in Jarvis's OWN Chrome window, which he can then drive ("open the first result",
# "add it to cart", "what's the price"). Chrome only allows that with a separate profile, so sign in to sites once
# there; logins stay. 0 = pages open in the normal default browser (no control), as before.
BROWSER_CONTROL = os.getenv("BROWSER_CONTROL", "1") != "0"
BROWSER_PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chrome-profile")
BROWSER_PORT = int(os.getenv("BROWSER_PORT", "9229"))
CHROME_PATH = os.getenv("CHROME_PATH") or next(
    (p for p in (os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                 os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                 os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")) if os.path.exists(p)), "")

# How to launch / close common apps. "launch" goes to the Windows `start` command,
# "process" is the .exe name used to close it. Add your own apps here.
APP_ALIASES = {
    "chrome": {"launch": "chrome", "process": "chrome.exe"},
    "google chrome": {"launch": "chrome", "process": "chrome.exe"},
    "edge": {"launch": "msedge", "process": "msedge.exe"},
    "vs code": {"launch": "code", "process": "Code.exe"},
    "vscode": {"launch": "code", "process": "Code.exe"},
    "visual studio code": {"launch": "code", "process": "Code.exe"},
    "spotify": {"launch": "spotify:", "process": "Spotify.exe"},
    "whatsapp": {"launch": "whatsapp:", "process": "WhatsApp.exe"},
    "notepad": {"launch": "notepad", "process": "notepad.exe"},
    "calculator": {"launch": "calc", "process": "CalculatorApp.exe"},
    "file explorer": {"launch": "explorer", "process": None},
    "explorer": {"launch": "explorer", "process": None},
    "settings": {"launch": "ms-settings:", "process": None},
    "terminal": {"launch": "wt", "process": "WindowsTerminal.exe"},
    "task manager": {"launch": "taskmgr", "process": "Taskmgr.exe"},
}

# Routines: one phrase, many actions. Only these run; Jarvis can't invent new shell commands.
ROUTINES = {
    "work mode": [
        {"tool": "open_app", "input": {"name": "vs code"}},
        {"tool": "open_app", "input": {"name": "chrome"}},
        {"tool": "open_app", "input": {"name": "spotify"}},
    ],
    "focus mode": [
        {"tool": "close_app", "input": {"name": "whatsapp"}},
        {"tool": "set_volume", "input": {"level": 20}},
    ],
}
