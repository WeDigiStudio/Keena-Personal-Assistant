"""Find installed apps and Steam games by (fuzzy) name, so "open last of us" really opens it.

Sources: Start Menu shortcuts (all users + you) and Steam library manifests.
The list is built once and cached for 10 minutes.
"""
import difflib
import glob
import os
import re
import sys
import time

_cache = {"time": 0.0, "apps": {}}
FILLER = re.compile(r"\b(the|app|application|game|games|program|software|launcher|desktop|pc|for|windows)\b")


NUMBERS = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
           "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
           "nine": "9", "ten": "10"}


def _norm(name):
    name = name.lower()
    name = re.sub(r"[™®©:_\-–.']", " ", name)
    name = FILLER.sub(" ", name)
    words = [NUMBERS.get(w, w) for w in name.split()]
    return " ".join(words)


def _initials(normed):
    """'grand theft auto 5' -> 'gta5' (so 'GTA 5' matches)."""
    return "".join(w if w.isdigit() else w[0] for w in normed.split())


def _start_menu_apps():
    apps = {}
    roots = [os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
             os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")]
    for root in roots:
        for path in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True) + \
                glob.glob(os.path.join(root, "**", "*.url"), recursive=True):
            title = os.path.splitext(os.path.basename(path))[0]
            if re.search(r"uninstall|readme|help|website|manual|documentation|release notes", title, re.I):
                continue
            apps.setdefault(title, path)
    return apps


def _steam_games():
    """Steam games from appmanifest files in every Steam library -> steam://rungameid/<id>."""
    games = {}
    steam_dirs = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam_dirs.append(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        pass
    steam_dirs += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    libraries = set()
    for d in steam_dirs:
        vdf = os.path.join(d, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            libraries.add(os.path.join(d, "steamapps"))
            try:
                with open(vdf, encoding="utf-8", errors="ignore") as f:
                    for p in re.findall(r'"path"\s+"([^"]+)"', f.read()):
                        libraries.add(os.path.join(p.replace("\\\\", "\\"), "steamapps"))
            except OSError:
                pass
    for lib in libraries:
        for acf in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
            try:
                with open(acf, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                appid = re.search(r'"appid"\s+"(\d+)"', text)
                name = re.search(r'"name"\s+"([^"]+)"', text)
                if appid and name and "redistributable" not in name[1].lower() and "steamworks" not in name[1].lower():
                    games[name[1]] = f"steam://rungameid/{appid[1]}"
            except OSError:
                pass
    return games


def _store_and_start_apps():
    """Windows' own list of every Start menu app, including Microsoft Store apps
    (Spotify, Calculator, Camera, Photos...) that have no normal shortcut file."""
    import json
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                         capture_output=True, text=True, timeout=30, creationflags=0x08000000)
    data = json.loads(out.stdout or "[]")
    if isinstance(data, dict):
        data = [data]
    apps = {}
    for d in data:
        name, appid = d.get("Name"), d.get("AppID")
        if name and appid:
            apps[name] = f"shell:AppsFolder\\{appid}"
    return apps


JUNK = re.compile(r"\.(txt|pdf|html?|chm|rtf|ini|log|url)$|^(license|readme|changelog|eula)\b", re.I)


def _desktop_shortcuts():
    apps = {}
    for root in [os.path.join(os.path.expanduser("~"), "Desktop"), os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop"),
                 os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")]:
        for path in glob.glob(os.path.join(root, "*.lnk")) + glob.glob(os.path.join(root, "*.url")):
            apps.setdefault(os.path.splitext(os.path.basename(path))[0], path)
    return apps


def _epic_games():
    """Epic Games Launcher installs -> com.epicgames.launcher://apps/<AppName>?action=launch"""
    import json
    games = {}
    folder = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Epic\EpicGamesLauncher\Data\Manifests")
    for item in glob.glob(os.path.join(folder, "*.item")):
        try:
            with open(item, encoding="utf-8") as f:
                data = json.load(f)
            name, app = data.get("DisplayName"), data.get("AppName")
            if name and app:
                games[name] = f"com.epicgames.launcher://apps/{app}?action=launch&silent=true"
        except (OSError, ValueError):
            pass
    return games


LIST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps_found.txt")


def installed():
    if time.time() - _cache["time"] > 600 or not _cache["apps"]:
        apps = {}
        if sys.platform == "win32":
            for source in (_start_menu_apps, _store_and_start_apps, _desktop_shortcuts, _steam_games, _epic_games):
                try:
                    apps.update(source())
                except Exception as e:
                    print(f"[apps] couldn't read {source.__name__}: {e}")
        apps = {t: p for t, p in apps.items() if not JUNK.search(t)}
        _cache.update(time=time.time(), apps=apps)
        try:  # so you (and Claude) can see what Jarvis can open
            with open(LIST_FILE, "w", encoding="utf-8") as f:
                f.write(f"# {len(apps)} apps/games Jarvis can open (rebuilt every start)\n")
                f.writelines(f"{t}  ->  {apps[t]}\n" for t in sorted(apps, key=str.lower))
        except OSError:
            pass
    return _cache["apps"]


STOP = {"of", "and", "a", "an", "to", "in", "on", "my", "for", "with", "part"}


def _score(want, title):
    a, b = _norm(want), _norm(title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(b.split()) >= 2 and a.replace(" ", "") == _initials(b):
        return 0.95
    wa, wb = set(a.split()), set(b.split())
    if wa <= wb:  # every word he said is in the title: strong match, fewer extra words = better
        return 0.97 - 0.01 * len(wb - wa)
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    key_a, key_b = wa - STOP, wb - STOP
    shared = key_a & key_b
    # also accept near-identical words ("ofice" ~ "office", "cyber punk" ~ "cyberpunk")
    if not shared:
        ja, jb = a.replace(" ", ""), b.replace(" ", "")
        if len(ja) >= 5 and jb.startswith(ja):   # "cyber punk" -> "cyberpunk 2077"
            return 0.9
        joined = difflib.SequenceMatcher(None, ja, jb).ratio()
        return joined if joined >= 0.86 else 0.0   # no real word in common: only near-identical names count
    overlap = len(shared) / max(len(key_a), 1)
    return 0.55 * overlap + 0.45 * ratio


def find_app(name, threshold=0.6):
    """Best matching installed app/game: (title, target, score) or None."""
    wants_game = bool(re.search(r"\bgames?\b", name.lower()))
    if wants_game:
        threshold = 0.5  # "...game": accept looser matches (he'll still be asked to confirm)
    best, best_score = None, 0.0
    for title, target in installed().items():
        s = _score(name, title)
        if wants_game and str(target).startswith("steam://"):
            s += 0.15  # he said "game": prefer actual games
        if s > best_score:
            best, best_score = (title, target), s
    return (*best, best_score) if best and best_score >= threshold else None


def launch(target):
    if str(target).startswith("shell:AppsFolder"):
        import subprocess
        subprocess.Popen(["explorer.exe", target])
    else:
        os.startfile(target)
