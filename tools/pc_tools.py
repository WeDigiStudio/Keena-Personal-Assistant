"""Everything Jarvis can physically do on the PC. Windows only.

Each tool is a plain Python function. TOOLS describes them to the brain.
Risky tools ask for a spoken "yes" first through the `confirm` callback.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime

import mss
import psutil
import pyautogui

from config import APP_ALIASES, BROWSER_CONTROL, ROUTINES, SCREENSHOT_DIR, USER_NAME

pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort


def _open(url):
    """Every page Jarvis opens goes to HIS browser window (tools/browser.py), so he can carry on: "open the first
    result", "add it to cart". If that fails for any reason: the normal browser, as before."""
    if BROWSER_CONTROL:
        try:
            from tools import browser
            browser.open_url(url)
            return
        except Exception as e:
            print(f"   [browser control failed, using the normal browser: {e}]")
    webbrowser.open(url)


def _alias(name):
    return APP_ALIASES.get(name.lower().strip(), {})


_last_open = 0.0  # when an app was last opened, so typing right after can wait for its window


def open_app(name, confirm=None, **_):
    """Open an app: known alias -> installed app/game by name -> Windows search as a last resort."""
    global _last_open
    from tools import apps
    launch = _alias(name).get("launch")
    if launch:
        subprocess.Popen(f'start "" {launch}', shell=True)
        _last_open = time.time()
        return f"Opened {name}."
    found = apps.find_app(name)
    if found:
        title, target, score = found
        if score < 0.85 and confirm and not confirm(f"Did you mean {title}?"):
            return f"Okay, not opening {title}. Say the name again, or check apps_found.txt in the Jarvis folder."
        apps.launch(target)
        _last_open = time.time()
        return f"Opening {title}."
    # Not found in Start Menu or Steam: try Windows search, and say so honestly
    pyautogui.press("win")
    time.sleep(0.6)
    pyautogui.write(name, interval=0.03)
    time.sleep(0.9)
    pyautogui.press("enter")
    _last_open = time.time()
    return f"I couldn't find {name} in your installed apps, so I tried Windows search. Check it opened."



# Apps that hold his documents: never force-killed (that would lose unsaved work)
DOCUMENT_APPS = {"notepad.exe", "winword.exe", "excel.exe", "powerpnt.exe", "code.exe", "photoshop.exe", "obs64.exe",
                 "wordpad.exe", "onenote.exe", "acrobat.exe", "figma.exe", "unity.exe", "devenv.exe"}


def _app_windows(proc, title_word):
    """Visible top-level windows of the app: by process, or for Store apps (hosted by ApplicationFrameHost) by title."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == proc.lower()}
    hosts = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "applicationframehost.exe"}
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if n and (pid.value in pids or (pid.value in hosts and title_word.lower() in buf.value.lower())):
                found.append(hwnd)
        return True
    user32.EnumWindows(each, 0)
    return found, pids


def close_app(name, confirm, **_):
    """Close an app the way clicking X does, so it can ask to save. It used to KILL the process: 'close notepad'
    would wipe unsaved text in every Notepad window. Document apps are never force-killed."""
    import ctypes
    proc = _alias(name).get("process") or f"{name.lower().replace(' ', '')}.exe"
    windows, pids = _app_windows(proc, name.split()[0])
    if not windows and not pids:
        return f"{name} wasn't running."
    if not confirm(f"Close {name}?"):
        return "User said no. Nothing closed."
    for hwnd in windows:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE: a polite "please close"
    for _ in range(30):
        time.sleep(0.1)
        if not _app_windows(proc, name.split()[0])[0]:
            return f"Closed {name}."
    if proc.lower() in DOCUMENT_APPS:
        return f"{name} is still open. It's probably asking whether to save your work."
    killed = 0
    for p in psutil.process_iter(["name"]):   # no windows, or it ignored the request: force it, as before
        if (p.info["name"] or "").lower() == proc.lower():
            try:
                p.terminate()
                killed += 1
            except psutil.Error:
                pass
    return f"Closed {name}." if killed or not _app_windows(proc, name.split()[0])[0] else f"I couldn't close {name}."


def search_web(query, **_):
    _open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Here are the results for {query}."


def open_website(url, **_):
    url = url.strip()
    host = url.split("://")[-1].split("/", 1)[0]
    if "." not in host and ":" not in host and host != "localhost":   # the AI may send "youtube": https://youtube goes nowhere
        url = url.replace(" ", "").rstrip("/") + ".com"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    _open(url)
    return f"Opened {url.split('://', 1)[-1].removeprefix('www.').rstrip('/')}."


# Search INSIDE a site ("open amazon" alone was useless: it couldn't search Amazon). {q} = the query, URL-encoded.
SITE_SEARCH = {
    "amazon": ("Amazon", "https://www.amazon.in/s?k={q}"),
    "flipkart": ("Flipkart", "https://www.flipkart.com/search?q={q}"),
    "youtube": ("YouTube", "https://www.youtube.com/results?search_query={q}"),
    "google": ("Google", "https://www.google.com/search?q={q}"),
    "github": ("GitHub", "https://github.com/search?q={q}&type=repositories"),
    "linkedin": ("LinkedIn", "https://www.linkedin.com/search/results/all/?keywords={q}"),
    "maps": ("Google Maps", "https://www.google.com/maps/search/{q}"),
    "wikipedia": ("Wikipedia", "https://en.wikipedia.org/w/index.php?search={q}"),
    "spotify": ("Spotify", "https://open.spotify.com/search/{q}"),
    "netflix": ("Netflix", "https://www.netflix.com/search?q={q}"),
    "twitter": ("X", "https://x.com/search?q={q}"),
    "reddit": ("Reddit", "https://www.reddit.com/search/?q={q}"),
    "gmail": ("Gmail", "https://mail.google.com/mail/u/0/#search/{q}"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/?q={q}"),
    "stack overflow": ("Stack Overflow", "https://stackoverflow.com/search?q={q}"),
    "myntra": ("Myntra", "https://www.myntra.com/{q}"),
    "swiggy": ("Swiggy", "https://www.swiggy.com/search?query={q}"),
    "zomato": ("Zomato", "https://www.zomato.com/search?q={q}"),
    "imdb": ("IMDb", "https://www.imdb.com/find/?q={q}"),
}
SITE_ALIASES = {"amazon.in": "amazon", "amazon.com": "amazon", "x": "twitter", "google maps": "maps", "yt": "youtube",
                "stackoverflow": "stack overflow", "wiki": "wikipedia", "mail": "gmail"}


def search_site(site, query, **_):
    key = SITE_ALIASES.get(site.lower().strip(), site.lower().strip())
    if key not in SITE_SEARCH:
        return f"I can't search inside {site} yet. I can search {', '.join(v[0] for v in SITE_SEARCH.values())}."
    label, template = SITE_SEARCH[key]
    q = query.strip(" .?!")
    enc = urllib.parse.quote("-".join(q.split())) if key == "myntra" else urllib.parse.quote_plus(q) \
        if "?" in template else urllib.parse.quote(q)
    _open(template.format(q=enc))
    if BROWSER_CONTROL:
        from tools import browser
        browser.set_topic(q)
    return f"Here's {q} on {label}."


def play_on_youtube(query, **_):
    """Open the FIRST VIDEO for the query, so it plays ("it opens YouTube search, it can't open the video").
    Read from YouTube's normal results page (~0.8 s); any problem -> the results page as before."""
    results = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    try:
        req = urllib.request.Request(results, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                                                       "Accept-Language": "en"})
        page = urllib.request.urlopen(req, timeout=6).read().decode("utf-8", "replace")
        m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"\}', page)
    except Exception:
        m = None
    if not m:
        _open(results)
        return f"Here's {query} on YouTube."
    _open(f"https://www.youtube.com/watch?v={m[1]}")
    title = re.split(r"\s+[|(\[]|\s+-{2}\s+", json.loads(f'"{m[2]}"'))[0].strip()   # "Believer (Official Video) | X" -> "Believer"
    title = re.sub(r"\s+", " ", re.sub(r"[^\w\s'&,.:-]", " ", title)).strip() or query   # "relax/study" -> "relax study"
    return f"Playing {title} on YouTube."


TERMINALS = {"windowsterminal.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe", "openconsole.exe"}


def _focus_is_terminal():
    """True if the focused window is a terminal (like the one Jarvis runs in).
    Typing into a terminal can RUN what's typed, so Jarvis refuses to type or press keys there."""
    if sys.platform != "win32":
        return False
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    if hwnd == ctypes.windll.kernel32.GetConsoleWindow():
        return True
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower() in TERMINALS
    except psutil.Error:
        return False


NOT_IN_TERMINAL = ("Sorry, the focused window is a terminal, so I won't type there; it could run as commands. "
                   "Click the window you want first.")


# ------------------------------------------------------------------ clipboard + Notepad drafts
def _clipboard(text=None):
    """Read (text=None) or set the Windows clipboard as Unicode text. Plain Win32 calls: no extra packages."""
    import ctypes
    from ctypes import wintypes
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    CF_UNICODETEXT = 13
    for _ in range(10):   # another app may hold the clipboard for a moment
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return None
    try:
        if text is None:
            handle = u32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = k32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(ptr)
            finally:
                k32.GlobalUnlock(handle)
        u32.EmptyClipboard()
        data = ctypes.create_unicode_buffer(text)
        handle = k32.GlobalAlloc(0x0002, ctypes.sizeof(data))   # GMEM_MOVEABLE
        ctypes.memmove(k32.GlobalLock(handle), data, ctypes.sizeof(data))
        k32.GlobalUnlock(handle)
        u32.SetClipboardData(CF_UNICODETEXT, handle)
        return text
    finally:
        u32.CloseClipboard()


def _bring_forward(win):
    """Bring a window to the front; True only if it really is in front afterwards (Windows may refuse)."""
    import pygetwindow as gw
    try:
        if win.isMinimized:
            win.restore()
        pyautogui.press("alt")   # Windows only lets a background app switch windows after a key press
        win.activate()
    except Exception:
        pass
    time.sleep(0.4)
    active = gw.getActiveWindow()
    return bool(active and active._hWnd == win._hWnd)


def _into_text(win):
    """Put the cursor in the window's text area. The Alt key used to switch windows leaves Notepad in MENU mode,
    where Ctrl+A / Ctrl+V do nothing (the paste silently didn't land). A click in our own window fixes both."""
    pyautogui.click(win.left + win.width // 2, win.top + max(win.height // 2, 120))
    time.sleep(0.15)


def notepad_window(hwnd=None):
    import pygetwindow as gw
    wins = [w for w in gw.getAllWindows() if w.title.endswith("Notepad")]
    if hwnd:
        return next((w for w in wins if w._hWnd == hwnd), None)
    return wins


def write_document(text, hwnd=None):
    """Put `text` into the Notepad window Jarvis uses for drafts (hwnd), REPLACING what's there; no hwnd or the
    window is gone -> a new Notepad. Pasted, not typed: instant, and his clipboard is put back.
    Returns (spoken result, hwnd). Never selects-all in a window Jarvis didn't open for the draft."""
    win = notepad_window(hwnd) if hwnd else None
    if not win:
        before = {w._hWnd for w in notepad_window()}
        subprocess.Popen("notepad")
        for _ in range(30):   # wait for the new window
            time.sleep(0.1)
            new = [w for w in notepad_window() if w._hWnd not in before]
            if new:
                win = new[0]
                break
        if not win:
            return "Notepad didn't open.", None
        time.sleep(0.3)
    if not _bring_forward(win):
        return "I couldn't bring the draft's Notepad window forward.", win._hWnd
    if _focus_is_terminal():
        return NOT_IN_TERMINAL, win._hWnd
    _into_text(win)
    saved = _clipboard()
    _clipboard(text.replace("\n", "\r\n"))
    pyautogui.hotkey("ctrl", "a")   # only ever in OUR draft window (checked above)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    if saved is not None:
        _clipboard(saved)
    # say "it's in Notepad" only if it really is (it once reported success with an empty window)
    back = read_document(win._hWnd)
    if not back or _squash(back)[:200] != _squash(text)[:200]:
        return "I couldn't put the text into Notepad.", win._hWnd
    return "It's in Notepad.", win._hWnd


def _squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


def read_document(hwnd):
    """The current text of the draft's Notepad window (with any edits he made by hand), or None."""
    win = notepad_window(hwnd)
    if not win or not _bring_forward(win) or _focus_is_terminal():
        return None
    _into_text(win)
    saved = _clipboard()
    _clipboard("")
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "c")
    text = ""
    for _ in range(15):   # Notepad can take a moment to put the copy on the clipboard (0.2 s fixed wasn't enough)
        time.sleep(0.1)
        text = _clipboard()
        if text:
            break
    pyautogui.press("right")   # drop the selection (cursor to the end)
    if saved is not None:
        _clipboard(saved)
    return text.replace("\r\n", "\n") if text else None


def email_text(to="", subject="", body=""):
    return f"To: {to or ''}\nSubject: {subject or ''}\n\n{(body or '').strip()}\n"


def parse_email(text):
    """'To: ...\\nSubject: ...\\n\\nbody' (as written to Notepad, maybe edited by him) -> (to, subject, body)."""
    to = re.search(r"^[ \t]*To:[ \t]*(.*)$", text, re.M | re.I)   # [ \t], not \s: an empty "To:" ate the next line
    subject = re.search(r"^[ \t]*Subject:[ \t]*(.*)$", text, re.M | re.I)
    body = re.sub(r"^\s*(?:To|Subject):.*$\n?", "", text, flags=re.M | re.I).strip()
    return (to[1].strip() if to else ""), (subject[1].strip() if subject else ""), body


BROWSERS = ("Google Chrome", "Microsoft Edge", "Mozilla Firefox", "Brave", "Opera", "Vivaldi")


def _browser_windows(site=None):
    """Browser windows, optionally only those whose FRONT tab is the site (Windows only shows the front tab's title)."""
    import pygetwindow as gw
    return [w for w in gw.getAllWindows() if w.title and any(w.title.endswith(b) for b in BROWSERS)
            and (not site or site.lower() in w.title.lower())]


def close_tab(site=None, **_):
    """Close a browser tab: the one showing `site` ("close the YouTube video"), or the front tab ("close this tab").
    Ctrl+W only after checking the window in front really is that browser tab - never a guess."""
    import pygetwindow as gw
    name = (site or "").strip()
    # Jarvis's own window first: its tabs can be closed even in the background ("close the Amazon tab" said
    # "I don't see an Amazon tab in front")
    if BROWSER_CONTROL:
        from tools import browser
        if browser.is_running() and (name or browser.is_front()):
            try:
                said = browser.close_tab(name or None)
                if said:
                    return said
            except Exception as e:
                print(f"   [browser close_tab: {e!r}]")
    wins = _browser_windows(name or None)
    said = {"youtube": "YouTube", "linkedin": "LinkedIn", "whatsapp": "WhatsApp"}.get(name.lower(), name.title())
    if not wins:
        return (f"I don't see a {said} tab in front in any browser window. It may be in a background tab."
                if name else "I don't see a browser window open.")
    active = gw.getActiveWindow()
    target = next((w for w in wins if active and w._hWnd == active._hWnd), None)
    if not target:
        if not name and len(wins) > 1:
            return "There are several browser windows. Which tab should I close?"
        target = wins[0]
        try:
            if target.isMinimized:
                target.restore()
            pyautogui.press("alt")   # Windows only lets a background app switch windows after a key press
            target.activate()
        except Exception:
            pass
        time.sleep(0.4)
        active = gw.getActiveWindow()
    if not active or active._hWnd != target._hWnd or (name and name.lower() not in active.title.lower()):
        return f"I couldn't bring the {said or 'browser'} window forward to close it."
    if _focus_is_terminal():
        return NOT_IN_TERMINAL
    title = re.sub(r"^\(\d+\)\s*|\s+-\s+(?:YouTube\s+-\s+)?(?:" + "|".join(BROWSERS) + r")$", "", active.title)
    pyautogui.hotkey("ctrl", "w")
    return f"Closed {title[:60]}." if title else "Closed the tab."


# ---- Driving Jarvis's browser window ("open the first result", "add it to cart", "scroll down")
def _browser_do(fn, *args, **kw):
    from tools import browser
    try:
        return fn(*args, **kw)
    except browser.BrowserError as e:
        return str(e)
    except Exception as e:
        print(f"   [browser: {e!r}]")
        return "Sorry, that didn't work in the browser."


def browser_open_result(n=1, which=None, **_):
    from tools import browser
    return _browser_do(browser.open_result, n, which)


def shop_for(query, site=None, **_):
    from tools import browser
    site = SITE_ALIASES.get((site or "").lower(), (site or "").lower()) or None
    if site and site not in browser.SHOPS:
        return search_site(site, query)
    return _browser_do(browser.shop, query.strip(" .?!"), site)


def browser_results(**_):
    from tools import browser
    return _browser_do(browser.list_results)


def browser_click(target, confirm=None, **_):
    from tools import browser
    return _browser_do(browser.click, target, confirm)


def browser_search(query, **_):
    from tools import browser
    return _browser_do(browser.search_here, query)


def scroll(direction="down", **_):
    """In Jarvis's browser when it's in front; otherwise the window in front (mouse wheel over its middle)."""
    from tools import browser
    if browser.is_front():
        return _browser_do(browser.scroll, direction)
    import pygetwindow as gw
    win = gw.getActiveWindow()
    if not win or _focus_is_terminal():
        return "There's no window in front to scroll." if not win else NOT_IN_TERMINAL
    if direction in ("top", "bottom"):
        pyautogui.press("home" if direction == "top" else "end")
        return "Back at the top." if direction == "top" else "At the bottom."
    pyautogui.moveTo(win.left + win.width // 2, win.top + win.height // 2)
    pyautogui.scroll(-700 if direction == "down" else 700)
    return "Scrolled down." if direction == "down" else "Scrolled up."


def browser_nav(action="back", **_):
    """Back / forward / reload in Jarvis's browser; any other browser in front gets the same keys; with no browser
    in front, "go back" stays what it always was: the previous track."""
    from tools import browser
    import pygetwindow as gw
    win = gw.getActiveWindow()
    # Jarvis's browser if it's in front or was just used (a test once sent Back to his own Chrome in front);
    # another browser in front -> its keys; no browser at all: "go back" is still the previous track
    if browser.is_front() or browser.recently_used():
        return _browser_do(browser.nav, action)
    other = win and any(win.title.endswith(b) for b in BROWSERS)
    if not other and browser.is_running():
        return _browser_do(browser.nav, action)
    if other:
        pyautogui.hotkey(*{"back": ("alt", "left"), "forward": ("alt", "right"), "reload": ("f5",)}[action])
        return {"back": "Gone back.", "forward": "Gone forward.", "reload": "Reloaded."}[action]
    if action == "back":
        return media_control("previous")
    return "There's no browser in front."


def type_text(text, **_):
    # Just opened an app? Give its window ~1.5 s to appear and take focus, so the text lands in it.
    # (Only waits here, so a plain "open chrome" stays instant.)
    wait = 1.5 - (time.time() - _last_open)
    if 0 < wait <= 1.5:
        time.sleep(wait)
    if _focus_is_terminal():
        return NOT_IN_TERMINAL
    pyautogui.write(text, interval=0.01)
    return "Typed it."


def draft_email(to="", subject="", body="", **_):
    """Open a Gmail compose window with everything filled in. Jarvis never sends: you review and click Send."""
    to = (to or "").strip()
    # The AI sometimes invents addresses (gopi@example.com). Only keep real-looking ones.
    if "@" not in to or re.search(r"@(?:example|test|email|domain|company|mail)\.(?:com|org|net)$|^(?:name|user|someone)@", to, re.I):
        to = ""
    params = {"view": "cm", "fs": "1", "to": to, "su": subject or "", "body": body or ""}
    url = "https://mail.google.com/mail/?" + urllib.parse.urlencode(
        {k: v for k, v in params.items() if v}, quote_via=urllib.parse.quote)
    _open(url)
    tail = "" if to else " Add the recipient's email address,"
    return f"Your draft is open in Gmail.{tail} check it, and click Send when you're happy."


def press_keys(keys, **_):
    if isinstance(keys, str):  # small models sometimes send "ctrl+c" instead of a list
        keys = keys.replace(" ", "").split("+")
    keys = [k.lower() for k in keys]
    bad = [k for k in keys if k not in pyautogui.KEYBOARD_KEYS]
    if bad:
        return f"Unknown keys: {bad}"
    if _focus_is_terminal():
        return NOT_IN_TERMINAL
    pyautogui.hotkey(*keys)
    return f"Pressed {'+'.join(keys)}."


def change_volume(step=10, **_):
    """'Turn it up' / 'quieter': the current level +/- step (there was no way to say this before)."""
    try:
        from pycaw.pycaw import AudioUtilities
        now = round(AudioUtilities.GetSpeakers().EndpointVolume.GetMasterVolumeLevelScalar() * 100)
    except Exception:
        pyautogui.press("volumeup" if int(step) > 0 else "volumedown", presses=max(1, abs(int(step)) // 2))
        return "Louder." if int(step) > 0 else "Quieter."
    return set_volume(max(0, min(100, now + int(step))))


def set_volume(level, **_):
    level = max(0, min(100, int(level)))
    try:  # exact volume through the Windows audio API
        from pycaw.pycaw import AudioUtilities
        speakers = AudioUtilities.GetSpeakers()
        try:
            endpoint = speakers.EndpointVolume  # newer pycaw
        except AttributeError:  # older pycaw
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            endpoint = cast(iface, POINTER(IAudioEndpointVolume))
        endpoint.SetMute(0, None)
        endpoint.SetMasterVolumeLevelScalar(level / 100, None)
        return f"Volume set to {level}%."
    except Exception:  # fallback: media keys, each press = 2%
        pyautogui.press("volumedown", presses=50, interval=0.005)
        pyautogui.press("volumeup", presses=round(level / 2), interval=0.005)
        return f"Volume set to about {level}%."


MEDIA_KEYS = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack", "mute": "volumemute"}


def media_control(action, **_):
    key = MEDIA_KEYS.get(action)
    if not key:
        return f"Unknown media action {action}."
    pyautogui.press(key)
    return f"Media: {action}."


def take_screenshot(**_):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png"))
    with mss.mss() as s:
        s.shot(mon=-1, output=path)  # -1 = all monitors
    os.startfile(path)
    return "Screenshot taken and saved in Pictures, Jarvis folder."


def system_status(**_):
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    msg = f"CPU {cpu}%, RAM {ram}%."
    batt = psutil.sensors_battery()
    if batt:
        msg += f" Battery {batt.percent}%{' charging' if batt.power_plugged else ''}."
    return msg


def lock_pc(**_):
    subprocess.run("rundll32.exe user32.dll,LockWorkStation")
    return "PC locked."


def sleep_pc(confirm, **_):
    if not confirm("Put the PC to sleep?"):
        return "User said no."
    subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
    return "Going to sleep."


def remember(fact, said="", **_):
    """Save a fact he asked Jarvis to remember (data/jarvis.db, with his words and the date).
    A newer fact about the same thing ("his mom's name is ...") replaces the older one."""
    from brain.memory import store
    if not fact.strip(" ."):
        return "Nothing to remember."
    return "Updated, I'll remember that." if store().add(fact, said) == "updated" else "Noted, I'll remember that."


def recall(**_):
    """'What do you know about me / what have I told you?' Read straight from memory, no AI: exact, never invented."""
    from brain.memory import store, to_you, when
    facts = store().all()
    if not facts:
        return "Beyond the profile you wrote for me, you haven't asked me to remember anything yet."
    said = "; ".join(to_you(f) + ("" if when(u) == "today" else f", since {when(u).removeprefix('on ')}")
                     for _, f, u in facts[:3])
    more = f" And {len(facts) - 3} more besides." if len(facts) > 3 else ""
    return f"Besides your profile, you've asked me to remember that {said}.{more}"


def forget_last(confirm, **_):
    """'Forget that': the most recently remembered fact, after he confirms."""
    from brain.memory import store, to_you
    facts = store().all()
    if not facts:
        return "There's nothing I've been asked to remember."
    i, fact, _ = facts[0]
    if not confirm(f"Forget that {to_you(fact)}?"):
        return "Very well, I'll keep it."
    store().forget([i])
    return "Forgotten."


def forget_about(topic, confirm, **_):
    """'Forget everything about Arjun': every matching fact, after he confirms the exact list."""
    from brain import summaries
    from brain.memory import _keywords, store, to_you, your
    facts = store().about(topic)
    talks = [s for s in summaries.store().all() if _keywords(topic) and _keywords(topic) <= _keywords(s[2])]
    if not facts and not talks:
        return f"I don't have anything about {your(topic)} that you asked me to remember."
    parts = []
    if facts:
        parts.append(f"{len(facts)} thing{'s' * (len(facts) > 1)} you told me: " + "; ".join(to_you(f) for _, f, _ in facts[:3])
                     + (f", and {len(facts) - 3} more" if len(facts) > 3 else ""))
    if talks:
        parts.append(f"{len(talks)} conversation summar{'ies' if len(talks) > 1 else 'y'} mentioning it")
    if not confirm(f"Forget {' and '.join(parts)}, about {your(topic)}?"):
        return "Very well, I'll keep them."
    store().forget([i for i, _, _ in facts])
    summaries.store().forget_about(topic)
    return (f"Forgotten. Nothing about {your(topic)} is left in my memory"
            + (", though the raw conversation log still has those words." if talks else "."))


def run_routine(name, confirm, **_):
    steps = ROUTINES.get(name.lower().strip())
    if not steps:
        return f"No routine called {name}. Available: {', '.join(ROUTINES)}"
    results = [run_tool(s["tool"], s["input"], confirm) for s in steps]
    return f"Routine {name}: " + " ".join(results)


FUNCTIONS = {
    "open_app": open_app,
    "close_app": close_app,
    "search_web": search_web,
    "web_answer": lambda question="", **_: "(answered by the brain)",   # needs the model: brain._web_answer runs it
    "close_tab": close_tab,
    "change_volume": change_volume,
    "open_website": open_website,
    "search_site": search_site,
    "browser_open_result": browser_open_result,
    "browser_results": browser_results,
    "shop_for": shop_for,
    "browser_click": browser_click,
    "browser_search": browser_search,
    "browser_nav": browser_nav,
    "scroll": scroll,
    "page_answer": lambda question="", **_: "(answered by the brain)",   # needs the model: brain._page_answer runs it
    "play_on_youtube": play_on_youtube,
    "type_text": type_text,
    "press_keys": press_keys,
    "set_volume": set_volume,
    "media_control": media_control,
    "take_screenshot": take_screenshot,
    "system_status": system_status,
    "lock_pc": lock_pc,
    "sleep_pc": sleep_pc,
    "run_routine": run_routine,
    "remember": remember,
    "draft_email": draft_email,
    # instant (router) only, deliberately not offered to the AI: it must never forget things on its own
    "recall": recall,
    "forget_last": forget_last,
    "forget_about": forget_about,
}

from tools import extras  # noqa: E402  reminders, notes, weather, folders, clipboard, power
FUNCTIONS.update(extras.FUNCTIONS)


def run_tool(name, args, confirm):
    fn = FUNCTIONS.get(name)
    if not fn:
        return f"Unknown tool {name}."
    try:
        return fn(confirm=confirm, **args)
    except Exception as e:  # never crash the voice loop because one action failed
        return f"Error in {name}: {e}"


def _tool(name, description, props=None, required=None):
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": props or {}, "required": required or []},
    }


TOOLS = [
    _tool("open_app", "Open an application on the PC, e.g. chrome, vs code, spotify, notepad, or any installed app.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("close_app", "Close a running application. Asks the user to confirm.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("web_answer", "Search the web, read the top pages and ANSWER his question out loud: prices, news, scores, "
          "recent events, comparisons, anything current or that you don't know. 'question' is his question in full.",
          {"question": {"type": "string"}}, ["question"]),
    _tool("search_web", "Open Google results in the browser. ONLY when he asks to open/see/show the results himself; "
          "to answer a question use web_answer.", {"query": {"type": "string"}}, ["query"]),
    _tool("open_website", "Open a website URL in the default browser.", {"url": {"type": "string"}}, ["url"]),
    _tool("search_site", "Search INSIDE a site and show the results: 'search amazon for a mouse' -> site='amazon', "
          f"query='mouse'. Sites: {', '.join(SITE_SEARCH)}.",
          {"site": {"type": "string"}, "query": {"type": "string"}}, ["site", "query"]),
    _tool("shop_for", "He wants to BUY a named product: finds it in the shop (amazon/flipkart/myntra) and opens its "
          "page. Never make up product links.", {"query": {"type": "string"}, "site": {"type": "string"}}, ["query"]),
    _tool("close_tab", "Close a browser tab: the one showing a site (site='youtube', 'gmail'...) or, with no site, "
          "the front tab. Use for 'close the YouTube video', 'close this tab'.", {"site": {"type": "string"}}),
    _tool("play_on_youtube", "Search YouTube for a song or video.", {"query": {"type": "string"}}, ["query"]),
    _tool("type_text", "Type text into whatever window is focused (English characters).",
          {"text": {"type": "string"}}, ["text"]),
    _tool("press_keys", "Press a key or shortcut, e.g. ['ctrl','c'], ['alt','tab'], ['enter'], ['win','d'].",
          {"keys": {"type": "array", "items": {"type": "string"}}}, ["keys"]),
    _tool("set_volume", "Set system volume, 0 to 100.", {"level": {"type": "integer"}}, ["level"]),
    _tool("media_control", "Control music or video playback.",
          {"action": {"type": "string", "enum": list(MEDIA_KEYS)}}, ["action"]),
    _tool("take_screenshot", "Take a screenshot of all screens and open it."),
    _tool("system_status", "Get CPU, RAM and battery status."),
    _tool("lock_pc", "Lock the PC."),
    _tool("sleep_pc", "Put the PC to sleep. Asks the user to confirm."),
    _tool("draft_email", "Write an email for him: it is typed into a Gmail compose window so he can see it and ask for "
          "changes. Use this whenever he asks to write, draft, send or change an email; for a change, call it again with "
          "the whole new version. If he asked to send it, Keena asks him to confirm, then sends it. "
          "'to' must be an email address he actually said or one in his profile / what he asked you to remember; "
          "NEVER make one up, leave it empty instead. "
          f"Write a complete, polished email from what he asked, even if his sentence trailed off. Sign off as {USER_NAME}.",
          {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["subject", "body"]),
    _tool("remember", "Permanently save a fact about the user (his work, people, preferences) when he asks "
          "you to remember something or corrects a fact about himself. Write it as a short third-person fact.",
          {"fact": {"type": "string"}}, ["fact"]),
    _tool("run_routine", f"Run a saved routine. Available: {', '.join(ROUTINES)}.",
          {"name": {"type": "string"}}, ["name"]),
]
TOOLS += extras.TOOLS
