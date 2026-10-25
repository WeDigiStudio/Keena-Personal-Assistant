"""Starts the orb HUD (hud.py) in its own process and sends it updates over a local UDP port.

If PySide6 isn't installed or the HUD crashes, every call here silently does nothing:
Jarvis itself keeps working.
"""
import json
import os
import socket
import subprocess
import sys

PORT = 47321
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOOL_CARDS = {
    "open_app": lambda a: f"Opening {a.get('name', '')}",
    "close_app": lambda a: f"Closing {a.get('name', '')}",
    "open_website": lambda a: f"Opening {a.get('url', '')}",
    "search_web": lambda a: f"Searching {a.get('query', '')}",
    "play_on_youtube": lambda a: f"YouTube: {a.get('query', '')}",
    "search_site": lambda a: f"{a.get('site', '').title()}: {a.get('query', '')}",
    "browser_open_result": lambda a: f"Opening the {a.get('which') or 'result ' + str(a.get('n', 1))}",
    "browser_click": lambda a: f"Clicking: {a.get('target', '')}",
    "browser_search": lambda a: f"Searching: {a.get('query', '')}",
    "page_answer": lambda a: "Reading the page",
    "browser_results": lambda a: "Reading the results",
    "set_volume": lambda a: f"Volume {a.get('level', '')}%",
    "media_control": lambda a: f"Media: {a.get('action', '').replace('_', '/')}",
    "type_text": lambda a: "Typing",
    "press_keys": lambda a: "Keys: " + "+".join(a.get("keys", [])) if isinstance(a.get("keys"), list) else "Keys",
    "take_screenshot": lambda a: "Screenshot",
    "system_status": lambda a: "System check",
    "set_reminder": lambda a: f"Reminder: {a.get('text', '')}",
    "set_timer": lambda a: f"Timer {a.get('minutes', '')} min",
    "take_note": lambda a: "Note saved",
    "remember": lambda a: "Remembered",
    "get_weather": lambda a: f"Weather {a.get('city', '') or ''}".strip(),
    "open_folder": lambda a: f"Folder: {a.get('name', '')}",
    "draft_email": lambda a: "Writing in Gmail",
    "send_draft": lambda a: "Sending",
    "read_clipboard": lambda a: "Reading clipboard",
    "look_up": lambda a: f"Wikipedia: {a.get('topic', '')}",
    "web_answer": lambda a: f"Researching: {a.get('question', '')}",   # not "Searching": that one opens a browser
    "show_in_calculator": lambda a: f"Calculator: {a.get('expression', '')}",
    "open_file": lambda a: f"File: {a.get('name', '')}",
}


def card_text(name, args):
    """The action tag for a tool call ('Opening chrome'): the PC HUD and the phone show the same ones."""
    make = TOOL_CARDS.get(name)
    try:
        return make(args) if make else name.replace("_", " ").title()
    except Exception:
        return name.replace("_", " ").title()


class Hud:
    def __init__(self, enabled=True, position="bottom-right", scale=1.0):
        self.proc = None
        self.booted = []   # boot lines so far: every boot message carries all of them (the HUD may miss early ones)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if not enabled:
            self.sock = None
            return
        try:
            import PySide6  # noqa: F401  (only checking it's installed)
        except ImportError:
            print("[HUD off: run  pip install PySide6  to get the on-screen orb]")
            self.sock = None
            return
        log = open(os.path.join(ROOT, "hud.log"), "w", encoding="utf-8")
        flags = 0x08000000 if sys.platform == "win32" else 0  # no console window for the HUD
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "hud.py"), "--parent", str(os.getpid()),
                                      "--position", str(position), "--scale", str(scale)],
                                     stdout=log, stderr=log, creationflags=flags)
        import atexit
        atexit.register(self.close)  # Ctrl+C / normal exit: close the orb too

    def send(self, **msg):
        if not self.sock:
            return
        try:
            self.sock.sendto(json.dumps(msg).encode("utf-8"), ("127.0.0.1", PORT))
        except OSError:
            pass

    def state(self, name, **extra):
        self.send(state=name, **extra)

    def boot(self, line):
        """Startup: one more part is ready ('HEARING Â· small.en Â· GPU'). Real details only."""
        self.booted.append(line)
        self.send(state="boot", boot=self.booted)

    def tool(self, name, args):
        self.send(card=card_text(name, args))

    def close(self):
        self.send(quit=True)
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=2)
            except Exception:
                self.proc.kill()
