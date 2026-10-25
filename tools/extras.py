"""Everyday features: reminders & timers, notes, spoken weather, folders, clipboard, power.

Everything here runs on your PC. Weather is the only one that uses the internet (wttr.in, no key).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMINDERS_FILE = os.path.join(ROOT, "reminders.json")
NOTES_FILE = os.path.join(ROOT, "notes.txt")
DEFAULT_CITY = os.getenv("JARVIS_CITY", "Chennai")

_lock = threading.Lock()


# ------------------------------------------------------------------ reminders & timers
def _load():
    try:
        with open(REMINDERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(items):
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)


def _clock(ts):
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def _parse_at(at):
    """'7 pm', '7:30pm', '19:30', '7' -> next datetime at that time (today or tomorrow)."""
    m = re.match(r"^\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s*$", str(at).lower())
    if not m:
        return None
    hour, minute, ampm = int(m[1]), int(m[2] or 0), (m[3] or "").replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    now = datetime.now()
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not ampm and hour <= 12 and when <= now:  # "at 7" in the evening means 7 pm
        when += timedelta(hours=12)
    if when <= now:
        when += timedelta(days=1)
    return when


def set_reminder(text, minutes=None, at=None, **_):
    """Remind in N minutes, or at a clock time ('7 pm')."""
    text = (text or "").strip().rstrip(".") or "your reminder"
    if at:
        when = _parse_at(at)
        if not when:
            return f"Sorry, I didn't understand the time {at}."
        ts = when.timestamp()
    else:
        try:
            ts = time.time() + float(minutes) * 60
        except (TypeError, ValueError):
            return "Sorry, how many minutes from now?"
    with _lock:
        items = _load()
        items.append({"time": ts, "text": text})
        _save(items)
    link = "about" if re.match(r"(?:the|a|an|my|your|his|our|this|that)\b|\w+ing\b", text, re.I) else "to"   # "about the gym"
    return f"Okay, I'll remind you {link} {text} at {_clock(ts)}."


def set_timer(minutes, **_):
    m = float(minutes)
    label = f"{int(m)} minute" if m >= 1 else f"{int(m * 60)} second"
    with _lock:
        items = _load()
        items.append({"time": time.time() + m * 60, "text": f"your {label} timer is done", "timer": True})
        _save(items)
    return f"Timer set for {label}s."


def list_reminders(**_):
    items = sorted(_load(), key=lambda r: r["time"])
    if not items:
        return "You have no reminders."
    parts = [f"a timer ending at {_clock(r['time'])}" if r.get("timer") else f"{r['text']} at {_clock(r['time'])}"
             for r in items[:5]]
    return f"You have {len(items)} reminder{'s' if len(items) > 1 else ''}: " + "; ".join(parts) + "."


def today_reminders():
    """[(text, timestamp)] still to come today (not timers), soonest first: for the startup greeting."""
    now = datetime.now()
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return sorted([(r["text"], r["time"]) for r in _load() if not r.get("timer") and time.time() <= r["time"] < end],
                  key=lambda x: x[1])


def clear_reminders(**_):
    with _lock:
        _save([])
    return "All reminders cleared."


def due_reminders():
    """Called by main.py about once a second. Returns the texts that are due now, and removes them."""
    now = time.time()
    with _lock:
        items = _load()
        due = [r for r in items if r["time"] <= now]
        if due:
            _save([r for r in items if r["time"] > now])
    out = []
    for r in due:
        late = now - r["time"] > 120
        if r.get("timer"):
            out.append(f"Sir, {r['text']}.")
        else:
            out.append(f"Sir, {'you missed a reminder: ' if late else 'reminder: '}{r['text']}.")
    return out


# ------------------------------------------------------------------ notes
def take_note(text, **_):
    text = (text or "").strip()
    if not text:
        return "What should I note down?"
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.now().strftime('%d %b %Y, %I:%M %p')}: {text}\n")
    return "Noted down."


def read_notes(count=5, **_):
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            notes = [ln.strip("- \n") for ln in f if ln.strip()]
    except OSError:
        notes = []
    if not notes:
        return "You don't have any notes yet."
    last = notes[-int(count):]
    spoken = [n.split(": ", 1)[-1] for n in last]
    return f"Your last {len(last)} note{'s' if len(last) > 1 else ''}: " + "; ".join(spoken) + "."


# ------------------------------------------------------------------ weather
def get_weather(city=None, day="today", **_):
    """Current weather, or the forecast for today/tomorrow/day after, including chance of rain."""
    city = (city or DEFAULT_CITY).strip()
    day = (day or "today").lower()
    idx = {"today": 0, "now": 0, "tomorrow": 1, "day after tomorrow": 2, "day after": 2}.get(day, 0)
    try:
        req = urllib.request.Request(f"https://wttr.in/{urllib.parse.quote(city)}?format=j1",
                                     headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        place = city.split(",")[0].title()
        fc = data["weather"][idx]
        rain = max(int(h.get("chanceofrain", 0)) for h in fc["hourly"])
        rain_txt = (f"{rain}% chance of rain" if rain >= 20 else "no rain expected")
        if idx == 0 and day in ("today", "now"):
            cur = data["current_condition"][0]
            desc = cur["weatherDesc"][0]["value"].strip().lower()
            return (f"In {place} it's {desc}, {cur['temp_C']} degrees, feels like {cur['FeelsLikeC']}. "
                    f"Today: {fc['mintempC']} to {fc['maxtempC']} degrees, {rain_txt}.")
        when = "Tomorrow" if idx == 1 else "Day after tomorrow"
        midday = fc["hourly"][len(fc["hourly"]) // 2]["weatherDesc"][0]["value"].strip().lower()
        return f"{when} in {place}: {midday}, {fc['mintempC']} to {fc['maxtempC']} degrees, {rain_txt}."
    except Exception:
        import webbrowser
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(f"weather {city} {day}"))
        return f"I couldn't fetch the weather, so I opened a search for {city}."


# ------------------------------------------------------------------ look up (Wikipedia)
def look_up(topic, **_):
    """Short, accurate summary of a person, place, company or thing from Wikipedia."""
    topic = (topic or "").strip()
    if not topic:
        return "What should I look up?"
    headers = {"User-Agent": "JarvisPersonalAssistant/1.0 (personal use)"}
    try:
        q = urllib.parse.urlencode({"action": "opensearch", "search": topic, "limit": 1, "namespace": 0, "format": "json"})
        req = urllib.request.Request(f"https://en.wikipedia.org/w/api.php?{q}", headers=headers)
        with urllib.request.urlopen(req, timeout=6) as r:
            titles = json.loads(r.read().decode("utf-8"))[1]
        if not titles:
            return f"I couldn't find {topic} on Wikipedia."
        title = urllib.parse.quote(titles[0].replace(" ", "_"))
        req = urllib.request.Request(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}", headers=headers)
        with urllib.request.urlopen(req, timeout=6) as r:
            extract = json.loads(r.read().decode("utf-8")).get("extract", "")
        extract = re.sub(r"\s*\([^)]*\)", "", extract)  # drop pronunciations/dates in brackets: easier to listen to
        sentences = re.split(r"(?<=[.!?])\s+", extract.strip())
        return " ".join(sentences[:2]) or f"I couldn't find details on {topic}."
    except Exception:
        return f"I couldn't reach Wikipedia to look up {topic}."


# ------------------------------------------------------------------ folders
FOLDERS = {
    "downloads": "Downloads", "download": "Downloads", "documents": "Documents", "document": "Documents",
    "desktop": "Desktop", "pictures": "Pictures", "photos": "Pictures", "music": "Music",
    "videos": "Videos", "screenshots": os.path.join("Pictures", "Jarvis"),
}


SKIP_DIRS = {"windows", "$recycle.bin", "system volume information", "programdata", "appdata", "node_modules",
             ".git", "__pycache__", "recovery", "perflogs", "$windows.~ws", "$winreagent"}


def _find_folder(name, drive=None):
    """Find a folder by name (any case): drive roots, your user folders, then 2 levels deep. Fast, not a full scan."""
    want = name.lower().strip()
    home = os.path.expanduser("~")
    roots = []
    if drive:
        roots.append(f"{drive.upper()}:\\")
    roots += [home, os.path.join(home, "Desktop"), os.path.join(home, "Documents"), os.path.join(home, "Downloads"),
              os.path.join(home, "OneDrive"), os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "OneDrive", "Documents")]
    if not drive:
        roots += [f"{d}:\\" for d in "CDEF" if os.path.isdir(f"{d}:\\")]
    seen = set()
    for depth in (1, 2):
        for root in roots:
            if not os.path.isdir(root):
                continue
            level = [root]
            for _ in range(depth - 1):
                nxt = []
                for folder in level:
                    try:
                        nxt += [e.path for e in os.scandir(folder) if e.is_dir(follow_symlinks=False)
                                and e.name.lower() not in SKIP_DIRS and not e.name.startswith(".")]
                    except OSError:
                        pass
                level = nxt[:400]
            for folder in level:
                if folder in seen:
                    continue
                seen.add(folder)
                try:
                    for e in os.scandir(folder):
                        if e.is_dir(follow_symlinks=False) and e.name.lower() == want:
                            return e.path
                except OSError:
                    pass
    return None


def open_folder(name, location=None, **_):
    """Open a known folder (downloads, documents...) or any folder by name, e.g. 'Games' on the C drive."""
    key = name.lower().strip().removesuffix(" folder").removeprefix("my ").removeprefix("the ").strip()
    drive = None
    m = re.search(r"\b([a-z])\s*(?:drive|:)", (location or "").lower())
    if m:
        drive = m[1]
    if key in ("jarvis", "jarvis folder", "project"):
        path = ROOT
    elif key in FOLDERS:
        sub = FOLDERS[key]
        path = os.path.join(os.path.expanduser("~"), sub)
        if not os.path.isdir(path):  # OneDrive often moves Documents/Desktop/Pictures
            alt = os.path.join(os.path.expanduser("~"), "OneDrive", sub)
            path = alt if os.path.isdir(alt) else path
    elif re.fullmatch(r"[a-z]", key) or re.fullmatch(r"[a-z] ?drive", key):  # "open D drive"
        path = f"{key[0].upper()}:\\"
    else:
        path = _find_folder(key, drive)
    if not path or not os.path.isdir(path):
        where = f" on the {drive.upper()} drive" if drive else ""
        return f"Sorry, I couldn't find a folder called {name}{where}."
    os.startfile(path)
    return f"Opened {os.path.basename(path.rstrip(chr(92))) or path}."


# ------------------------------------------------------------------ calculator
def show_in_calculator(expression, answer=None, **_):
    """Open Windows Calculator, type the sum and press =. Also says the answer."""
    import pyautogui
    from tools import pc_tools
    expr = re.sub(r"\s+", "", str(expression))
    if not re.fullmatch(r"[\d.+\-*/()]+", expr) or "**" in expr:
        return f"That's {answer}." if answer else "Sorry, I can only type plain sums into the calculator."
    subprocess.Popen('start "" calc', shell=True)
    # Type ONLY into Calculator: it used to wait 1.6 s and type into whatever window was in front
    import pygetwindow as gw
    calc = None
    for _ in range(30):
        time.sleep(0.15)
        calc = next((w for w in gw.getAllWindows() if w.title.strip() == "Calculator"), None)
        if calc:
            break
    in_front = bool(calc) and (getattr(gw.getActiveWindow(), "_hWnd", None) == calc._hWnd or pc_tools._bring_forward(calc))
    if not in_front or pc_tools._focus_is_terminal():
        return f"That's {answer}. I opened Calculator but couldn't type into it safely." if answer else \
            "I opened Calculator, but couldn't type into it safely."
    pyautogui.press("esc")  # clear whatever was there
    pyautogui.write(expr + "=", interval=0.04)
    return f"That's {answer}. It's on the calculator." if answer else "It's on the calculator."


# ------------------------------------------------------------------ files
MEDIA = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".mp3", ".wav", ".m4a", ".flac", ".aac"}


def _spoken_filename(name):
    """'final underscore 1' -> 'final_1', 'report dash final dot pdf' -> 'report-final.pdf'"""
    n = name.lower().strip()
    n = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine|zero)\b",
               lambda m: str(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"].index(m[1])), n)
    for word, sym in (("underscore", "_"), ("under score", "_"), ("dash", "-"), ("hyphen", "-"), ("dot", ".")):
        n = re.sub(rf"\s*\b{word}\b\s*", sym, n)
    return n.strip()


def _squash(s):
    return re.sub(r"[\s_\-.()\[\]]+", "", s.lower())


def find_file(name, folder=None):
    import difflib
    want = _spoken_filename(name)
    key = _squash(os.path.splitext(want)[0])
    nums = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
            "eight": "8", "nine": "9", "ten": "10"}
    alt = _squash(re.sub(r"\b(" + "|".join(nums) + r")\b", lambda m: nums[m[1]], os.path.splitext(want)[0]))
    keys = {k for k in (key, alt) if k}
    home = os.path.expanduser("~")
    folders = []
    if folder:
        sub = FOLDERS.get(folder.lower().rstrip("s") + "s", FOLDERS.get(folder.lower()))
        if sub:
            folders = [os.path.join(home, sub), os.path.join(home, "OneDrive", sub)]
    if not folders:
        folders = [os.path.join(home, s) for s in ("Downloads", "Desktop", "Documents", "Videos", "Music", "Pictures")]
    best, best_score, seen = None, 0.0, 0
    for root in folders:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, files in os.walk(root):
            if dirpath.count(os.sep) - root.count(os.sep) >= 3:
                dirnames[:] = []
            for f in files:
                seen += 1
                if seen > 20000:
                    break
                stem = _squash(os.path.splitext(f)[0])
                if not stem:
                    continue
                score = max(1.0 if stem == k else 0.9 if k in stem else difflib.SequenceMatcher(None, k, stem).ratio()
                            for k in keys)
                if os.path.splitext(f)[1].lower() in MEDIA:
                    score += 0.02  # "play X" usually means a video/song
                if score > best_score:
                    best, best_score = os.path.join(dirpath, f), score
    return best if best_score >= 0.75 else None


def open_file(name, folder=None, app=None, **_):
    """Open or play a file from your folders, with its default app or VLC."""
    path = find_file(name, folder)
    where = f" in your {folder} folder" if folder else ""
    if not path:
        return f"Sorry, I couldn't find a file called {_spoken_filename(name)}{where}."
    base = os.path.basename(path)
    if app and "vlc" in app.lower():
        for vlc in (r"C:\Program Files\VideoLAN\VLC\vlc.exe", r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"):
            if os.path.isfile(vlc):
                subprocess.Popen([vlc, path])
                return f"Playing {base} in VLC."
    os.startfile(path)
    return f"Playing {base}." if os.path.splitext(base)[1].lower() in MEDIA else f"Opened {base}."


# ------------------------------------------------------------------ clipboard
def read_clipboard(**_):
    text = ""
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
    except Exception:
        try:
            text = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                                  capture_output=True, text=True, timeout=5).stdout
        except Exception:
            text = ""
    text = (text or "").strip()
    if not text:
        return "Your clipboard is empty, or it isn't text."
    return f"Clipboard text: {text[:3000]}"


# ------------------------------------------------------------------ power
def power(action, confirm, **_):
    if sys.platform != "win32":
        return "That only works on Windows."
    if action == "restart":
        if not confirm("Restart the PC?"):
            return "Okay, not restarting."
        subprocess.Popen("shutdown /r /t 10", shell=True)
        return "Restarting in 10 seconds."
    if not confirm("Shut down the PC?"):
        return "Okay, not shutting down."
    subprocess.Popen("shutdown /s /t 10", shell=True)
    return "Shutting down in 10 seconds. Bye!"


FUNCTIONS = {
    "set_reminder": set_reminder, "set_timer": set_timer, "list_reminders": list_reminders,
    "clear_reminders": clear_reminders, "take_note": take_note, "read_notes": read_notes,
    "get_weather": get_weather, "look_up": look_up, "open_folder": open_folder,
    "show_in_calculator": show_in_calculator, "open_file": open_file, "read_clipboard": read_clipboard, "power": power,
}


def _tool(name, description, props=None, required=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("set_reminder", "Remind him later. Give 'minutes' from now OR 'at' a clock time like '7 pm' or '19:30'.",
          {"text": {"type": "string"}, "minutes": {"type": "number"}, "at": {"type": "string"}}, ["text"]),
    _tool("set_timer", "Start a countdown timer.", {"minutes": {"type": "number"}}, ["minutes"]),
    _tool("list_reminders", "Say his upcoming reminders."),
    _tool("clear_reminders", "Delete all reminders."),
    _tool("take_note", "Save a note (a to-do, idea or thing to follow up).", {"text": {"type": "string"}}, ["text"]),
    _tool("read_notes", "Read his latest notes.", {"count": {"type": "integer"}}),
    _tool("get_weather", f"Weather now, or the forecast for today/tomorrow (temperatures and chance of rain). "
          f"City defaults to {DEFAULT_CITY}.",
          {"city": {"type": "string"}, "day": {"type": "string", "enum": ["today", "tomorrow", "day after tomorrow"]}}),
    _tool("look_up", "Look up a real person, place, company, event or thing on Wikipedia (e.g. 'Narendra Modi', "
          "'Tesla', 'Chennai'). Use it for 'who is', 'what is', 'tell me about' questions about the world.",
          {"topic": {"type": "string"}}, ["topic"]),
    _tool("show_in_calculator", "Type a sum into Windows Calculator and press =. 'expression' uses only digits and "
          "+ - * / ( ), e.g. '5+5'. Use when he wants it done IN the calculator.",
          {"expression": {"type": "string"}}, ["expression"]),
    _tool("open_file", "Open or play a file from his folders (Downloads, Desktop, Documents, Videos...) by name. "
          "'final underscore 1' means final_1. Optional folder, and app='vlc' if he asks for VLC.",
          {"name": {"type": "string"}, "folder": {"type": "string"}, "app": {"type": "string"}}, ["name"]),
    _tool("open_folder", "Open a folder: downloads, documents, desktop, pictures, music, videos, screenshots, "
          "a drive ('D drive'), or ANY folder by its name (it searches). Put the drive in 'location' if he says one, "
          "e.g. name='Games', location='C drive'.",
          {"name": {"type": "string"}, "location": {"type": "string"}}, ["name"]),
    _tool("read_clipboard", "Get the text he copied, e.g. to read, summarize, translate or reply to it."),
    _tool("power", "Shut down or restart the PC. Asks him to confirm.",
          {"action": {"type": "string", "enum": ["shutdown", "restart"]}}, ["action"]),
]

# Tools whose raw output the brain should rework (summarize etc.) before speaking
NEEDS_FOLLOWUP = {"read_clipboard"}
# Tools whose output is a complete spoken answer (no "Done, sir." in front)
SPOKEN_TOOLS = {"show_in_calculator", "open_file", "look_up", "open_folder", "set_reminder", "set_timer", "list_reminders", "clear_reminders", "take_note", "read_notes",
                "get_weather", "power"}
