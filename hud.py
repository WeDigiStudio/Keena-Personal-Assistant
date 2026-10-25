"""Jarvis HUD: an arc-reactor style display in the corner of the screen that shows what Jarvis is doing.

  python hud.py --demo         preview it on its own (cycles through every state)
  python hud.py --demo --big   preview big screen mode (Esc or click to go back to the corner)

Normally main.py starts this automatically and talks to it over a local UDP port.
It never takes focus or blocks clicks, and if it crashes, Jarvis keeps working.

Messages (JSON over UDP): state, level (0-1 mic loudness), user (live transcript), reply,
card (an action being done), stat (how long the last reply took), quit.
Every number on screen is real: clock, CPU, memory, last response time.
"""
import json
import math
import random
import socket
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget

PORT = 47321
W, H = 420, 560
CX, CY = W / 2, 200
CYAN = QColor(255, 77, 166)         # Keena pink (the name CYAN stayed: it is "the accent")
DEEP = QColor(186, 85, 255)         # thinking: violet
TEXT = QColor(255, 236, 247)
DIM = QColor(214, 160, 190)
LABELS = {"listening": "LISTENING", "thinking": "PROCESSING", "speaking": "RESPONDING", "wake": "ONLINE",
          "boot": "INITIALISING"}
SMALL_FLAGS = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
               | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
BIG_FLAGS = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
# action cards (voice/hud_client.py TOOL_CARDS) that put a new window on screen
OPENS_WINDOW = ("Opening", "YouTube", "Searching", "Folder", "File", "Gmail", "Wikipedia", "Calculator")
FONT = "Bahnschrift SemiCondensed"
try:
    from config import ASSISTANT_NAME
except Exception:   # the HUD must start even if the settings fail
    ASSISTANT_NAME = "Keena"
TITLE = ".".join(ASSISTANT_NAME.upper())   # K.E.E.N.A   # ships with Windows 10/11; Qt falls back quietly if missing


def rgba(c, a):
    return QColor(c.red(), c.green(), c.blue(), max(0, min(255, int(a))))


def font(size, bold=False, spacing=0.0, family=FONT):
    f = QFont(family, size)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


class Hud(QWidget):
    def __init__(self, demo=False, parent=None, position="bottom-right", scale=1.0):
        super().__init__(None, SMALL_FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.mode = "small"      # small = corner, click-through; big = full screen ("big screen mode")
        self.position, self.scale = position, scale   # corner HUD: HUD_POSITION / HUD_SCALE in .env
        self.boot_lines = []     # startup: parts that are ready ("HEARING · small.en · GPU")
        self._place()

        self.state = "idle"
        self.opacity, self.target = 0.0, 0.0
        self.level, self.level_target = 0.0, 0.0
        self.user_text, self.reply_text, self.reply_shown = "", "", 0.0
        self.cards = []          # (text, time)
        self.hide_at = None
        self.spin = 0.0          # main rotation, speed depends on state
        self.boot = 0.0          # 0 -> 1 when the HUD appears: rings draw themselves in
        self.wave = [0.0] * 72   # listening: the core ring ripples with the mic level
        self.last = time.time()
        self.stat = ""           # "1.4s": how long the last reply took
        self.cpu = self.mem = None
        self.next_sys = 0.0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", PORT))
        self.sock.setblocking(False)

        self.demo = demo
        self.demo_step, self.demo_next = 0, time.time() + 0.5
        self.started = time.time()
        self.parent = parent           # Jarvis's process id: if it dies, the HUD closes itself
        self.next_parent_check = time.time() + 2

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)     # ~60 fps
        self.show()

    # ------------------------------------------------------------------ big / small
    def _place(self):
        if self.mode == "big":
            self.setGeometry(QApplication.primaryScreen().geometry())
        else:
            geo = QApplication.primaryScreen().availableGeometry()
            w, h = int(W * self.scale), int(H * self.scale)
            x = geo.left() + 16 if "left" in self.position else geo.right() - w - 16
            y = geo.top() + 16 if "top" in self.position else geo.bottom() - h - 16
            self.setGeometry(x, y, w, h)

    def set_mode(self, mode):
        if mode == self.mode or mode not in ("big", "small"):
            return
        self.mode = mode
        # big: takes clicks/keys (Esc or a click goes back). small: click-through, never takes focus.
        self.setWindowFlags(BIG_FLAGS if mode == "big" else SMALL_FLAGS)
        self._place()
        self.show()
        if mode == "big":
            self.activateWindow()
            self.target, self.hide_at, self.boot = 1.0, None, 0.0   # stay up, replay the rings drawing in
        elif self.state == "idle":
            self.hide_at = time.time() + 0.6

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.set_mode("small")

    def mousePressEvent(self, _):
        self.set_mode("small")

    # ------------------------------------------------------------------ messages
    def handle(self, msg):
        if "mode" in msg:
            self.set_mode(msg["mode"])
        if "boot" in msg:
            self.boot_lines = list(msg["boot"])   # the whole list each time: a missed packet costs nothing
        if msg.get("state") not in (None, "boot", "wake"):
            self.boot_lines = []                  # first real use: the boot list has done its job
        if "state" in msg:
            st = msg["state"]
            if st == "idle":
                self.hide_at = None if self.mode == "big" else time.time() + 1.2   # big screen stays up
                self.state = "idle"
            else:
                if self.state == "idle" or self.opacity < 0.05:  # waking up: fresh screen
                    self.user_text, self.reply_text, self.cards = "", "", []
                    if self.opacity < 0.05:
                        self.boot = 0.0
                self.state, self.target, self.hide_at = st, 1.0, None
                if st == "listening":
                    self.user_text = msg.get("user", "")
                    self.cards = [c for c in self.cards if time.time() - c[1] < 7]
        if "level" in msg:
            self.level_target = max(0.0, min(1.0, float(msg["level"])))
        if "user" in msg and "state" not in msg:
            if msg["user"] and not self.user_text:
                self.reply_text = ""  # you started talking again: clear Jarvis's previous answer
            self.user_text = msg["user"]
        if "reply" in msg:
            keep = self.reply_text and msg["reply"].startswith(self.reply_text)  # streamed: keep typing on
            self.reply_text = msg["reply"]
            self.reply_shown = self.reply_shown if keep else 0.0
            self.pace = bool(msg.get("pace"))   # a sentence she's saying now: type at her speaking pace
        if "card" in msg:
            self.cards = (self.cards + [(msg["card"], time.time())])[-3:]
            if self.mode == "big" and msg["card"].startswith(OPENS_WINDOW):
                self.set_mode("small")   # the new window would open BEHIND a full-screen HUD
        if "stat" in msg:
            self.stat = str(msg["stat"])

    def read_socket(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(65536)
            except (BlockingIOError, OSError):
                return
            try:
                msg = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if msg.get("quit"):
                QApplication.quit()
                return
            self.handle(msg)

    def run_demo(self):
        boot = ["SYSTEMS", "VOICE · Ryan", "HEARING · small.en · GPU", "BRAIN · qwen3:8b", "MEMORY · 2 facts · 9 conversations",
                "MICROPHONE"]
        script = [(0.6, {"state": "boot", "boot": boot[:i]}) for i in range(1, len(boot) + 1)] + [
            (2.2, {"state": "wake", "reply": "Good evening! Keena online."}),
            (1.0, {"state": "idle"}),
            (1.5, {}),
            (0.8, {"state": "listening"}),
            (2.6, {"user": "Hey Keena, open Chrome and"}),
            (0.6, {"user": "Hey Keena, open Chrome and play Anirudh on YouTube"}),
            (0.6, {"state": "thinking"}),
            (1.6, {"card": "Opening Chrome"}),
            (0.4, {"card": "YouTube: Anirudh"}),
            (0.2, {"state": "speaking", "reply": "Right away, sir. Here's Anirudh on YouTube.", "stat": "1.4s"}),
            (3.5, {"state": "listening", "user": ""}),
            (2.4, {"user": "What makes a good founder?"}),
            (0.6, {"state": "thinking"}),
            (0.6, {"state": "speaking", "reply": "Clarity under pressure, sir."}),
            (1.0, {"reply": "Clarity under pressure, sir. And the nerve to change course when the facts do."}),
            (3.5, {"state": "idle", "stat": "1.1s"}),
            (2.5, {}),
        ]
        if time.time() < self.demo_next:
            return
        wait, msg = script[self.demo_step % len(script)]
        self.handle(msg)
        self.demo_step += 1
        self.demo_next = time.time() + wait

    # ------------------------------------------------------------------ animation
    def tick(self):
        try:
            self._tick()
        except KeyboardInterrupt:  # Ctrl+C in the terminal: close cleanly
            QApplication.quit()

    def _tick(self):
        now = time.time()
        dt, self.last = min(now - self.last, 0.1), now
        self.read_socket()
        if self.parent and now >= self.next_parent_check:
            self.next_parent_check = now + 2
            if not _alive(self.parent):
                QApplication.quit()
                return
        if self.demo:
            if now - self.started > 60:  # demo closes itself after a couple of loops
                QApplication.quit()
                return
            self.run_demo()
            if self.state == "listening":
                self.level_target = abs(math.sin(now * 3.1)) * random.uniform(0.3, 1.0)
        if self.hide_at and now >= self.hide_at:
            self.target, self.hide_at = 0.0, None
        if self.state == "speaking":  # no access to the speaker level: animate a natural "voice" wobble
            self.level_target = 0.35 + 0.65 * abs(math.sin(now * 5.3) * math.sin(now * 2.1 + 1))
        if self.state in ("thinking", "idle"):
            self.level_target = 0.0
        self.opacity += (self.target - self.opacity) * min(1.0, dt * 9)
        self.level += (self.level_target - self.level) * min(1.0, dt * 14)
        if self.target > 0:
            self.boot = min(1.0, self.boot + dt * 1.6)
        speed = {"thinking": 220, "listening": 24, "speaking": 45}.get(self.state, 14)
        self.spin = (self.spin + dt * speed) % 360
        # the voice ripple: new samples enter at one point and travel round the ring
        self.wave = self.wave[1:] + [self.level * random.uniform(0.55, 1.0) if self.state == "listening" else 0.0]
        if self.reply_text:
            # typewriter: with her voice 16 characters/s (Ava measured 14.8/s; slightly ahead reads naturally), else fast
            speed = 16 if getattr(self, "pace", False) else 55
            self.reply_shown = min(len(self.reply_text), self.reply_shown + dt * speed)
        if self.opacity > 0.01 and now >= self.next_sys:   # real system load, once a second
            self.next_sys = now + 1.0
            try:
                import psutil
                self.cpu, self.mem = psutil.cpu_percent(None), psutil.virtual_memory().percent
            except Exception:
                self.cpu = self.mem = None
        if self.opacity > 0.005 or self.target > 0:
            self.update()

    # ------------------------------------------------------------------ drawing
    def paintEvent(self, _):
        if self.opacity < 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        t = time.time()
        accent = DEEP if self.state == "thinking" else CYAN
        boot = _ease(self.boot)
        if self.mode == "big":
            self._paint_big(p, accent, boot, t)
            p.end()
            return
        p.scale(self.scale, self.scale)   # HUD_SCALE: everything is drawn at 420x560, then scaled
        self._frame(p, accent, boot)
        self._readouts(p, accent, boot)
        self._reactor(p, accent, boot, t)
        y = self._cards(p, accent, CY + 166)
        y = self._boot_list(p, accent, y, 8, 0, W)
        self._text(p, accent, y)
        p.end()

    def _boot_list(self, p, accent, y, size, x, width, align=Qt.AlignHCenter):
        """Startup checklist: each part appears with a tick as it comes online. Returns where text continues."""
        if not self.boot_lines or self.state not in ("boot", "wake"):
            return y
        p.setFont(font(size, True, 2))
        for i, name in enumerate(self.boot_lines):
            done = i < len(self.boot_lines) - 1 or self.state == "wake"
            p.setPen(rgba(accent if done else TEXT, 220))
            mark = "✓" if done else "…"   # the newest one is still settling until Jarvis is ready
            p.drawText(QRectF(x, y, width, size * 2.2), _flags(align), f"{name}  {mark}")
            y += size * 2.2
        return y + 6

    def _paint_big(self, p, accent, boot, t):
        """Full screen: the same reactor, scaled up, with readouts in the corners and the conversation below."""
        w, h = self.width(), self.height()
        # backdrop: near-black with a faint grid and a glow behind the reactor
        p.fillRect(QRectF(0, 0, w, h), QColor(10, 3, 10, 238))
        p.setPen(QPen(rgba(accent, 10), 1))
        for x in range(0, w, 64):
            p.drawLine(QPointF(x, 0), QPointF(x, h))
        for y in range(0, h, 64):
            p.drawLine(QPointF(0, y), QPointF(w, y))
        rx, ry = w / 2, h * 0.43
        s = h * 0.30 / 132   # outer ring = 30% of the screen height
        vg = QRadialGradient(QPointF(rx, ry), h * 0.6)
        vg.setColorAt(0.0, rgba(accent, 26))
        vg.setColorAt(1.0, rgba(accent, 0))
        p.fillRect(QRectF(0, 0, w, h), vg)

        # screen-corner brackets
        m, L = 28, 70
        p.setPen(QPen(rgba(accent, 200 * boot), 2.5))
        for (x, y, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * L))

        # title
        p.setPen(rgba(TEXT, 235))
        p.setFont(font(26, True, 18))
        p.drawText(QRectF(0, h * 0.04, w, 44), _flags(Qt.AlignHCenter), TITLE)
        p.setPen(rgba(DIM, 200 * boot))
        p.setFont(font(10, False, 5))
        p.drawText(QRectF(0, h * 0.04 + 46, w, 20), _flags(Qt.AlignHCenter), "YOUR PERSONAL AI COMPANION")

        # corner readouts (real numbers only)
        p.setPen(rgba(TEXT, 230 * boot))
        p.setFont(font(34, True, 2))
        p.drawText(QRectF(m + 26, m + 18, 400, 50), _flags(Qt.AlignLeft), time.strftime("%H:%M:%S"))
        p.setPen(rgba(DIM, 220 * boot))
        p.setFont(font(12, False, 4))
        p.drawText(QRectF(m + 28, m + 70, 400, 22), _flags(Qt.AlignLeft), time.strftime("%A %d %B %Y").upper())
        rows = []
        if self.cpu is not None:
            rows += [("CPU", f"{self.cpu:.0f}%", self.cpu), ("MEMORY", f"{self.mem:.0f}%", self.mem)]
        if self.stat:
            rows.append(("RESPONSE", self.stat, 0))
        p.setFont(font(12, True, 3))
        for i, (name, val, pct) in enumerate(rows):
            y = m + 18 + i * 40
            p.setPen(rgba(QColor(255, 170, 60) if pct >= 90 else DIM, 220 * boot))
            p.drawText(QRectF(w - m - 330, y, 300, 22), _flags(Qt.AlignRight), f"{name}  {val}")
            if name != "RESPONSE":   # a thin load bar under CPU / memory
                p.setPen(QPen(rgba(accent, 60), 3))
                p.drawLine(QPointF(w - m - 180, y + 27), QPointF(w - m - 30, y + 27))
                p.setPen(QPen(rgba(QColor(255, 170, 60) if pct >= 90 else accent, 220), 3))
                p.drawLine(QPointF(w - m - 180, y + 27), QPointF(w - m - 180 + 150 * min(pct, 100) / 100, y + 27))
        p.setPen(rgba(DIM, 150 * boot))
        p.setFont(font(9, False, 3))
        p.drawText(QRectF(0, h - m - 22, w - m - 26, 18), _flags(Qt.AlignRight), "ESC OR CLICK  —  BACK TO CORNER")

        # the reactor, scaled
        p.save()
        p.translate(rx, ry)
        p.scale(s, s)
        p.translate(-CX, -CY)
        self._reactor(p, accent, boot, t)
        p.restore()

        # actions + conversation, large, centred under the reactor
        y = ry + (CY + 166 - CY) * s + 10
        now = time.time()
        p.setFont(font(13, True, 3))
        for text, born in [c for c in self.cards if now - c[1] <= 7]:
            p.setOpacity(self.opacity * max(0.0, min(1.0, 7 - (now - born))))
            p.setPen(rgba(accent, 230))
            p.drawText(QRectF(0, y, w, 24), _flags(Qt.AlignHCenter), f"[ ✓ {text.upper()[:40]} ]")
            y += 28
        p.setOpacity(self.opacity)
        self._boot_list(p, accent, h * 0.36, 12, m + 28, w * 0.25, Qt.AlignLeft)   # left side: the centre is the reactor
        tw = min(1200, w * 0.62)
        tx = (w - tw) / 2
        if self.user_text:
            p.setPen(rgba(DIM, 220))
            p.setFont(font(14))
            p.drawText(QRectF(tx, y + 6, tw, 52), _flags(Qt.AlignHCenter, Qt.TextWordWrap), _tail(self.user_text, 160))
            y += 60
        if self.reply_text:
            shown = self.reply_text[:int(self.reply_shown)]
            cursor = "█" if int(self.reply_shown) < len(self.reply_text) else ""
            p.setPen(TEXT)
            p.setFont(font(22, True))
            p.drawText(QRectF(tx, y + 6, tw, max(60, h - y - 60)), _flags(Qt.AlignHCenter, Qt.TextWordWrap),
                       _tail(shown, 220) + cursor)

    def _frame(self, p, accent, boot):
        """Dark cut-corner panel with bracket corners: readable over any wallpaper."""
        c = 18
        r = QRectF(8, 8, W - 16, H - 16)
        panel = QPolygonF([QPointF(r.left() + c, r.top()), QPointF(r.right() - c, r.top()),
                           QPointF(r.right(), r.top() + c), QPointF(r.right(), r.bottom() - c),
                           QPointF(r.right() - c, r.bottom()), QPointF(r.left() + c, r.bottom()),
                           QPointF(r.left(), r.bottom() - c), QPointF(r.left(), r.top() + c)])
        p.setPen(QPen(rgba(accent, 55), 1))
        p.setBrush(QColor(14, 4, 12, 205))
        p.drawPolygon(panel)
        # bright bracket corners
        p.setPen(QPen(rgba(accent, 200 * boot), 2))
        L = 26
        for (x, y, dx, dy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                               (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
            p.drawLine(QPointF(x + dx * c, y), QPointF(x + dx * (c + L), y))
            p.drawLine(QPointF(x, y + dy * c), QPointF(x, y + dy * (c + L)))
            p.drawLine(QPointF(x + dx * c, y), QPointF(x, y + dy * c))
        # title
        p.setPen(rgba(TEXT, 230))
        p.setFont(font(11, True, 6))
        p.drawText(QRectF(0, 22, W, 20), _flags(Qt.AlignHCenter), TITLE)
        p.setPen(rgba(accent, 120))
        p.drawLine(QPointF(CX - 70 * boot, 47), QPointF(CX + 70 * boot, 47))

    def _readouts(self, p, accent, boot):
        """Small real-data panels in the top corners, joined to the reactor by leader lines."""
        p.setFont(font(8, False, 1.5))
        left = [time.strftime("%H:%M:%S"), time.strftime("%a %d %b").upper()]
        right = []
        if self.cpu is not None:
            right += [f"CPU {self.cpu:3.0f}%", f"MEM {self.mem:3.0f}%"]
        if self.stat:
            right.append(f"RESP {self.stat}")
        for i, s in enumerate(left):
            p.setPen(rgba(TEXT if i == 0 else DIM, 220 * boot))
            p.drawText(QRectF(26, 66 + i * 15, 120, 14), _flags(Qt.AlignLeft), s)
        for i, s in enumerate(right):
            hot = s.startswith(("CPU", "MEM")) and float(s.split()[1].rstrip("%")) >= 90
            p.setPen(rgba(QColor(255, 170, 60) if hot else DIM, 220 * boot))   # amber when it's genuinely high
            p.drawText(QRectF(W - 146, 66 + i * 15, 120, 14), _flags(Qt.AlignRight), s)
        p.setPen(QPen(rgba(accent, 70 * boot), 1))
        for side in (-1, 1):   # leader lines: corner readout -> outer ring
            x0 = CX + side * 182
            p.drawLine(QPointF(x0, 100), QPointF(x0, 112))
            p.drawLine(QPointF(x0, 112), QPointF(CX + side * 112, 128))

    def _reactor(self, p, accent, boot, t):
        center = QPointF(CX, CY)
        lvl = self.level
        p.setBrush(Qt.NoBrush)

        # soft glow behind everything, breathing with the voice
        glow_r = (150 + 30 * lvl) * boot
        g = QRadialGradient(center, max(glow_r, 1))
        g.setColorAt(0.0, rgba(accent, 90))
        g.setColorAt(0.45, rgba(accent, 28))
        g.setColorAt(1.0, rgba(accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(center, glow_r, glow_r)
        p.setBrush(Qt.NoBrush)

        # outer ring with four notch markers
        R = 132 * boot
        p.setPen(QPen(rgba(accent, 110), 1.2))
        p.drawEllipse(center, R, R)
        p.setBrush(rgba(accent, 200))
        p.setPen(Qt.NoPen)
        for k in range(4):
            a = math.radians(k * 90 + 45)
            tip = QPointF(CX + math.cos(a) * (R - 7), CY + math.sin(a) * (R - 7))
            side = math.radians(k * 90 + 45 + 90)
            base = QPointF(CX + math.cos(a) * (R + 2), CY + math.sin(a) * (R + 2))
            p.drawPolygon(QPolygonF([tip, base + QPointF(math.cos(side) * 5, math.sin(side) * 5),
                                     base - QPointF(math.cos(side) * 5, math.sin(side) * 5)]))
        p.setBrush(Qt.NoBrush)

        # thinking: a radar sweep scanning the dial
        if self.state == "thinking":
            sweep = QConicalGradient(center, -self.spin * 1.4)
            sweep.setColorAt(0.0, rgba(accent, 110))
            sweep.setColorAt(0.12, rgba(accent, 0))
            sweep.setColorAt(1.0, rgba(accent, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(sweep)
            p.drawEllipse(center, R - 2, R - 2)
            p.setBrush(Qt.NoBrush)

        # dial: 72 ticks, every 6th long, turning slowly the other way
        p.save()
        p.translate(center)
        p.rotate(-self.spin * 0.35)
        for k in range(int(72 * boot)):
            long_tick = k % 6 == 0
            p.setPen(QPen(rgba(accent, 210 if long_tick else 90), 2 if long_tick else 1))
            r0 = 108 if long_tick else 112
            a = math.radians(k * 5)
            p.drawLine(QPointF(math.cos(a) * r0, math.sin(a) * r0), QPointF(math.cos(a) * 118, math.sin(a) * 118))
        p.restore()

        # segmented arcs at two radii, counter-rotating; faster when thinking
        for i, (rad, width, segs, span, direction) in enumerate(((96, 3.0, 3, 70, 1), (84, 1.6, 5, 38, -1))):
            rect = QRectF(CX - rad, CY - rad, 2 * rad, 2 * rad)
            p.setPen(QPen(rgba(accent, 230 - i * 70), width, Qt.SolidLine, Qt.FlatCap))
            start = self.spin * (1 + i * 0.6) * direction
            for s in range(segs):
                a0 = start + s * 360 / segs
                p.drawArc(rect, int(-a0 * 16), int(-span * boot * 16))

        # voice ring: listening = live ripple; speaking = radial equalizer; otherwise a calm circle
        base_r = 64
        if self.state == "listening":
            path = QPainterPath()
            n = len(self.wave)
            for k in range(n + 1):
                v = sum(self.wave[(k + d) % n] for d in (-2, -1, 0, 1, 2)) / 5   # smoothed: a ripple, not static
                a = math.radians(k * 360 / n + self.spin)
                r = base_r + 3 + 22 * v
                pt = QPointF(CX + math.cos(a) * r, CY + math.sin(a) * r)
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            p.setPen(QPen(rgba(accent, 235), 2))
            p.drawPath(path)
        elif self.state == "speaking":
            bars = 48
            for k in range(bars):
                a = math.radians(k * 360 / bars + self.spin * 0.4)
                wob = 0.5 + 0.5 * math.sin(t * 9 + k * 0.9) * math.sin(t * 4.3 + k * 0.37)
                length = 4 + 24 * lvl * wob
                p.setPen(QPen(rgba(accent, 220), 2.4, Qt.SolidLine, Qt.FlatCap))
                p.drawLine(QPointF(CX + math.cos(a) * base_r, CY + math.sin(a) * base_r),
                           QPointF(CX + math.cos(a) * (base_r + length), CY + math.sin(a) * (base_r + length)))
        else:
            p.setPen(QPen(rgba(accent, 120), 1.2))
            p.drawEllipse(center, base_r, base_r)

        # arc-reactor coil: 10 segments around the core
        p.save()
        p.translate(center)
        p.rotate(self.spin * 0.6)
        coil = QRectF(-48, -48, 96, 96)
        p.setPen(QPen(rgba(accent, 150 + 100 * lvl), 7, Qt.SolidLine, Qt.FlatCap))
        for k in range(10):
            p.drawArc(coil, int(k * 36 * 16), int(26 * boot * 16))
        p.restore()
        p.setPen(QPen(rgba(accent, 160), 1))
        p.drawEllipse(center, 40, 40)

        # core: hot white centre fading to cyan
        core_r = (30 + 6 * lvl + 1.5 * math.sin(t * 2.2)) * boot
        cg = QRadialGradient(center, max(core_r, 1))
        cg.setColorAt(0.0, QColor(255, 255, 255, 255))
        cg.setColorAt(0.35, QColor(255, 214, 236, 240))
        cg.setColorAt(0.75, rgba(accent, 220))
        cg.setColorAt(1.0, rgba(accent, 40))
        p.setPen(Qt.NoPen)
        p.setBrush(cg)
        p.drawEllipse(center, core_r, core_r)
        p.setBrush(Qt.NoBrush)

        # status under the reactor
        label = LABELS.get(self.state, "STANDBY")
        p.setFont(font(9, True, 4))
        p.setPen(rgba(accent, 230))
        p.drawText(QRectF(0, CY + 144, W, 18), _flags(Qt.AlignHCenter), label)
        dot_x = CX - p.fontMetrics().horizontalAdvance(label) / 2 - 14   # status light, drawn (fonts lack the glyph)
        on = not (self.state == "thinking" and int(t * 3) % 2)
        p.setPen(QPen(rgba(accent, 230), 1.2))
        p.setBrush(rgba(accent, 230) if on else Qt.NoBrush)
        p.drawEllipse(QPointF(dot_x, CY + 153), 3.5, 3.5)
        p.setBrush(Qt.NoBrush)

    def _text(self, p, accent, y):
        if self.user_text:   # what you said (live)
            p.setFont(font(8, True, 2))
            p.setPen(rgba(DIM, 200))
            p.drawText(QRectF(30, y, 60, 14), _flags(Qt.AlignLeft), "YOU")
            p.setFont(font(10))
            p.setPen(rgba(TEXT, 170))
            p.drawText(QRectF(30, y + 14, W - 60, 40), _flags(Qt.AlignLeft, Qt.TextWordWrap), _tail(self.user_text, 110))
            y += 58
        if self.reply_text:  # what Jarvis says (typewriter)
            p.setPen(QPen(rgba(accent, 160), 2))
            p.drawLine(QPointF(24, y + 2), QPointF(24, y + 64))   # accent bar beside the reply
            p.setFont(font(8, True, 2))
            p.setPen(rgba(accent, 220))
            p.drawText(QRectF(30, y, 80, 14), _flags(Qt.AlignLeft), ASSISTANT_NAME.upper())
            p.setFont(font(11, True))
            p.setPen(TEXT)
            shown = self.reply_text[:int(self.reply_shown)]
            cursor = "█" if int(self.reply_shown) < len(self.reply_text) else ""
            p.drawText(QRectF(30, y + 14, W - 60, 56), _flags(Qt.AlignLeft, Qt.TextWordWrap), _tail(shown, 130) + cursor)

    def _cards(self, p, accent, y):
        """Actions being done, as bracketed tags under the status line. Returns where the text starts."""
        now = time.time()
        live = [(text, born) for text, born in self.cards if now - born <= 7]
        p.setFont(font(8, True, 1.5))
        y += 6
        for text, born in live:
            age = now - born
            fade = max(0.0, min(1.0, 7 - age)) * min(1.0, age * 5)   # slide in, fade out
            p.setOpacity(self.opacity * fade)
            label = f"[ ✓ {text.upper()[:28]} ]"
            p.setPen(rgba(accent, 230))
            p.drawText(QRectF(0, y + 4 * (1 - min(1.0, age * 5)), W, 16), _flags(Qt.AlignHCenter), label)
            y += 18
        p.setOpacity(self.opacity)
        return y + 6


def _ease(x):
    return 1 - (1 - x) ** 3


def _alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        return True


def _flags(*flags):
    out = 0
    for f in flags:
        out |= int(getattr(f, "value", f))
    return out


def _tail(text, limit):
    """Keep the END of long text visible (that's the part being spoken/typed right now)."""
    return text if len(text) <= limit else "…" + text[-limit:]


def main():
    import signal
    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, lambda *_: QApplication.quit())  # Ctrl+C closes it quietly
    try:
        def arg(name, default):
            return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
        parent = int(arg("--parent", 0)) or None
        try:
            scale = min(2.0, max(0.6, float(arg("--scale", 1.0))))
        except ValueError:
            scale = 1.0
        hud = Hud(demo="--demo" in sys.argv, parent=parent, position=arg("--position", "bottom-right"), scale=scale)
        if "--big" in sys.argv:   # python hud.py --demo --big : preview big screen mode
            hud.set_mode("big")
    except OSError:
        print("HUD already running (port busy).")
        return
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
