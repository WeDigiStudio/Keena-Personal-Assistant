"""Jarvis Remote: control Jarvis from his phone.

A small web server inside Jarvis. The phone opens a private page (add it to the home screen) and types or dictates
commands; they go through the SAME brain as voice, with the same guards. Questions Jarvis asks ("Shall I put the
PC to sleep?") come back to the phone as Yes / No buttons.

Security:
- every request needs the secret key (data/remote_key.txt, made once; compared in constant time)
- only Tailscale addresses (100.64.0.0/10), this PC, and with REMOTE_LAN the home network may connect
- one turn at a time: the lock is shared with the voice loop, so phone and voice never run the brain together
"""
import hmac
import io
import ipaddress
import json
import os
import queue
import re
import secrets
import socket
import subprocess
import threading
import time
import uuid
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from config import ASSISTANT_NAME as NAME
except Exception:
    NAME = "Keena"
KEY_FILE = os.path.join(ROOT, "data", "remote_key.txt")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
ANSWER_WAIT = 90     # seconds he has to tap Yes / No
TURN_WAIT = 120      # longest a phone request waits for a reply


def key():
    """The secret key, made once."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            k = f.read().strip()
        if len(k) >= 20:
            return k
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    k = secrets.token_urlsafe(24)
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(k)
    return k


def tailscale_ip():
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return out.splitlines()[0] if out and out[0].isdigit() else None
    except Exception:
        return None


def tailscale_https():
    """https://<pc>.<tailnet>.ts.net if 'tailscale serve' forwards to Jarvis (a real certificate: the phone mic works)."""
    try:
        out = subprocess.run(["tailscale", "serve", "status"], capture_output=True, text=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return None
    m = re.search(r"(https://[\w.-]+\.ts\.net)\S*", out or "")
    return m[1] if m else None


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.168.0.1", 9))   # no packet is sent: just picks the interface to the home network
            return s.getsockname()[0]
        except OSError:
            return None


def urls(port, https_port=None):
    """Pairing addresses: Tailscale (works anywhere) first, then the home network. https when available (the mic
    needs it)."""
    scheme, p = ("https", https_port) if https_port else ("http", port)
    out = []
    if ip := tailscale_ip():
        out.append(("anywhere (Tailscale)", f"{scheme}://{ip}:{p}/#k={key()}"))
    if ip := lan_ip():
        out.append(("home Wi-Fi", f"{scheme}://{ip}:{p}/#k={key()}"))
    return out


class Turn:
    """One phone command running through the brain. Questions and the final reply go out through `out`."""

    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.out = queue.Queue()
        self.answers = queue.Queue()
        self.cards = []          # action tags for the phone's HUD ("Opening chrome"), like the PC's
        self.started = time.time()

    def card(self, name, args):
        from voice.hud_client import card_text
        self.cards.append(card_text(name, args))
        if name in SLOW_TOOLS and not getattr(self, "acked", False):
            # "Keena, mail my CTO..." -> "On it." right away (shot 2), not after the mail is typed
            self.acked = True
            self.out.put({"progress": "On it.", "turn": self.id, "cards": list(self.cards)})

    def confirm(self, question):
        self.out.put({"question": question, "turn": self.id})
        try:
            yes = self.answers.get(timeout=ANSWER_WAIT)
        except queue.Empty:
            return False   # no answer: the safe default is "no"
        return bool(yes)


# tools that take a while (typing a mail): the phone hears "On it." first
SLOW_TOOLS = ("draft_email", "shop_for", "web_answer", "page_answer")
YES = re.compile(r"\b(?:yes|yeah|yep|yup|sure|do it|go ahead|confirm|okay|ok|please do|absolutely)\b", re.I)
NO = re.compile(r"\b(?:no|nope|don'?t|do not|cancel|stop|wait|never mind)\b", re.I)


class Remote:
    """run(text, confirm, speak) -> reply is supplied by main.py (it holds the brain, the lock and the HUD).
    hear(audio bytes) -> text and voice(text) -> mp3 bytes make the phone a voice remote (None = text only)."""

    def __init__(self, run, port, lan=True, screenshot=None, hear=None, voice=None, https_port=None):
        self.run, self.port, self.lan, self.screenshot = run, port, lan, screenshot
        self.https_port = https_port
        self.hear, self.voice = hear, voice
        self.turns = {}
        self.audio = {}   # id -> (time, mp3): Jarvis's spoken replies, fetched by the phone once
        self.key = key()
        self.server = None

    # ------------------------------------------------------------------ voice
    def with_voice(self, msg):
        """Add Jarvis's voice to a reply or question, so the phone can play it."""
        said = msg.get("question") or msg.get("reply") or msg.get("progress")
        if not (self.voice and said):
            return msg
        try:
            mp3, spoken = self.voice(said)
        except Exception as e:
            print(f"   [phone voice failed: {e!r}]")
            return msg
        if spoken != said:   # Tamil mode: show what he hears
            msg = dict(msg, shown=spoken)
        now = time.time()
        for k in [k for k, (t, _) in self.audio.items() if now - t > 300]:
            self.audio.pop(k, None)
        aid = uuid.uuid4().hex
        self.audio[aid] = (now, mp3)
        self.last_said = (said, now)
        return dict(msg, audio=f"/api/audio/{aid}")

    def _echo(self, heard):
        """The phone's mic caught Jarvis's own reply from its speaker ("it repeats the dialogue twice")."""
        said, when = getattr(self, "last_said", ("", 0))
        if not said or time.time() - when > 60:
            return False
        a, b = re.sub(r"\W+", " ", heard.lower()).strip(), re.sub(r"\W+", " ", said.lower()).strip()
        return len(a) > 6 and (a in b or SequenceMatcher(None, a, b).ratio() > 0.7)

    def voice_turn(self, data, turn_id=None, speak=False, wake=False):
        """He spoke on the phone: transcribe on the PC, then a normal turn (or the answer to Jarvis's question).
        wake=True: the phone is waiting for "Hey Jarvis" (same detector as the PC): only that starts anything."""
        if not self.hear:
            return {"error": "voice isn't available in this mode"}
        t0 = time.time()
        heard = (self.hear(data, strip_wake=not wake) or "").strip()
        if wake:
            from voice.stt import find_wake
            command = find_wake(heard) if heard else None
            if command is None:
                return {"wake": False}   # ordinary talk in the room: ignored, not even printed
            print(f"   [phone: \"{NAME}\" heard ({heard!r})]")
            if not command:
                return {"wake": True, "heard": ""}
            from voice import language
            if language.needs_gemini(command, getattr(self.hear, "last_confidence", 0.0)):   # the command may be Tamil
                command = self.hear(data) or command
            heard = command   # "Hey Jarvis, play Believer" in one breath: straight to the command
        else:
            print(f"   [phone voice heard in {time.time() - t0:.1f}s: {heard!r}]")
        if not heard:
            return {"heard": ""}
        if self._echo(heard):
            print("   [phone: ignored, that was Jarvis's own voice]")
            return {"heard": "", "echo": True}
        if wake:
            return dict(self.with_voice(self.start_turn(heard, speak)), heard=heard, wake=True)
        if turn_id:
            yes = bool(YES.search(heard)) and not NO.search(heard)
            return dict(self.with_voice(self.answer(turn_id, yes)), heard=heard)
        return dict(self.with_voice(self.start_turn(heard, speak)), heard=heard)

    # ------------------------------------------------------------------ turns
    def start_turn(self, text, speak):
        turn = Turn()
        self.turns[turn.id] = turn

        def work():
            try:
                reply = self.run(text, turn.confirm, speak, turn.card)
            except Exception as e:
                print(f"   [remote error: {e!r}]")
                reply = "Sorry, something went wrong on the PC."
            turn.out.put({"reply": reply, "turn": turn.id, "done": True, "cards": turn.cards,
                          "stat": f"{time.time() - turn.started:.1f}s"})

        threading.Thread(target=work, daemon=True, name="remote-turn").start()
        return self.next_message(turn)

    def next_message(self, turn):
        try:
            msg = turn.out.get(timeout=TURN_WAIT)
        except queue.Empty:
            msg = {"reply": "Still working on it.", "turn": turn.id, "done": True}
        if msg.get("done"):
            self.turns.pop(turn.id, None)
        return msg

    def follow(self, turn_id):
        """After a progress message ("On it."): wait for what comes next in the same turn."""
        turn = self.turns.get(turn_id)
        if not turn:
            return {"reply": "", "done": True}
        return self.next_message(turn)

    def answer(self, turn_id, yes):
        turn = self.turns.get(turn_id)
        if not turn:
            return {"reply": "That question has expired.", "done": True}
        turn.answers.put(bool(yes))
        return self.next_message(turn)

    # ------------------------------------------------------------------ server
    def allowed(self, ip):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return a.is_loopback or (a.version == 4 and (a in TAILSCALE or (self.lan and a.is_private)))

    def start(self):
        remote = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Jarvis"
            sys_version = ""

            def log_message(self, *_):   # quiet: the voice console is busy enough
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(data)

            def _ok_client(self):
                if not remote.allowed(self.client_address[0]):
                    self._send(403, {"error": "not allowed from this network"})
                    return False
                return True

            def _ok_key(self):
                given = self.headers.get("X-Jarvis-Key", "")
                if not hmac.compare_digest(given.encode(), remote.key.encode()):
                    time.sleep(0.5)   # slow down guessing
                    self._send(401, {"error": "wrong key: pair again with python -m remote.pair"})
                    return False
                return True

            def do_GET(self):
                if not self._ok_client():
                    return
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):   # the page itself holds no secrets; the key stays on the phone
                    with open(PAGE, "rb") as f:
                        page = f.read().replace(b"__NAME_UPPER__", NAME.upper().encode()).replace(b"__NAME__", NAME.encode())
                        return self._send(200, page, "text/html; charset=utf-8")
                if path == "/manifest.json":
                    return self._send(200, MANIFEST, "application/manifest+json")
                if path == "/icon.svg":
                    return self._send(200, ICON, "image/svg+xml")
                if not self._ok_key():
                    return
                if path == "/api/status":
                    import psutil   # real numbers for the phone HUD's readouts, like the PC's
                    return self._send(200, {"online": True, "pc": socket.gethostname(), "voice": bool(remote.hear),
                                            "cpu": psutil.cpu_percent(None), "mem": psutil.virtual_memory().percent})
                if path.startswith("/api/audio/"):
                    item = remote.audio.pop(path.rsplit("/", 1)[-1], None)
                    return self._send(200, item[1], "audio/mpeg") if item else self._send(404, {"error": "gone"})
                if path == "/api/screen":
                    png = remote.screenshot() if remote.screenshot else None
                    return self._send(200, png, "image/jpeg") if png else self._send(500, {"error": "no screenshot"})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._ok_client() or not self._ok_key():
                    return
                length = int(self.headers.get("Content-Length") or 0)
                path, _, query = self.path.partition("?")
                if path == "/api/voice":   # raw audio from the phone's mic (webm/opus), at most ~4 MB (~4 min)
                    if not 0 < length <= 4_000_000:
                        return self._send(413, {"error": "recording too long"})
                    data = self.rfile.read(length)
                    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                    return self._send(200, remote.voice_turn(data, params.get("turn"), params.get("speak") == "1",
                                                             params.get("wake") == "1"))
                if length > 10_000:
                    return self._send(413, {"error": "too long"})
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(400, {"error": "bad json"})
                voiced = bool(body.get("voice"))   # the phone is in voice mode: send Jarvis's voice back too
                if path == "/api/ask":
                    text = str(body.get("text", "")).strip()[:500]
                    if not text:
                        return self._send(400, {"error": "empty"})
                    msg = remote.start_turn(text, bool(body.get("speak")))
                    return self._send(200, remote.with_voice(msg) if voiced else msg)
                if path == "/api/answer":
                    msg = remote.answer(str(body.get("turn", "")), body.get("yes"))
                    return self._send(200, remote.with_voice(msg) if voiced else msg)
                if path == "/api/next":   # the rest of a turn after "On it."
                    msg = remote.follow(str(body.get("turn", "")))
                    return self._send(200, remote.with_voice(msg) if voiced else msg)
                self._send(404, {"error": "not found"})

        self.server = _Quiet(("0.0.0.0", self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="remote-http").start()
        if self.https_port:
            # the phone's mic only works on a secure page: the same server over https, with a certificate this PC
            # made for itself (the phone warns once; the connection is still encrypted)
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*certificate())
            self.secure = _Quiet(("0.0.0.0", self.https_port), Handler)
            # handshake in the request's own thread, so one slow phone can't hold up the others
            self.secure.socket = ctx.wrap_socket(self.secure.socket, server_side=True, do_handshake_on_connect=False)
            threading.Thread(target=self.secure.serve_forever, daemon=True, name="remote-https").start()
        return self


class _Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):   # dropped connections / bad handshakes: not worth a traceback
        pass


CERT = os.path.join(ROOT, "data", "remote_cert.pem")
CERT_KEY = os.path.join(ROOT, "data", "remote_cert_key.pem")


def certificate():
    """(cert, key) files for https, made once by this PC for its own addresses. Made again if the home IP changed
    (the phone would see a name mismatch)."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    ips = {"127.0.0.1"} | {ip for ip in (lan_ip(), tailscale_ip()) if ip}
    if os.path.exists(CERT) and os.path.exists(CERT_KEY):
        with open(CERT, "rb") as f:
            old = x509.load_pem_x509_certificate(f.read())
        try:
            have = {str(a) for a in old.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                    .get_values_for_type(x509.IPAddress)}
        except x509.ExtensionNotFound:
            have = set()
        if ips <= have and old.not_valid_after_utc > datetime.datetime.now(datetime.timezone.utc):
            return CERT, CERT_KEY
    key_ = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Jarvis on {socket.gethostname()}")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key_.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.DNSName(socket.gethostname())] +
                                                       [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(ips)]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key_, hashes.SHA256()))
    os.makedirs(os.path.dirname(CERT), exist_ok=True)
    with open(CERT_KEY, "wb") as f:
        f.write(key_.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    with open(CERT, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return CERT, CERT_KEY


def make_hear(stt):
    """Phone recording (webm/ogg/mp4 from the browser) -> text, with the PC's Whisper (same hallucination filter)."""
    from faster_whisper import decode_audio

    def hear(data, strip_wake=True):
        from voice import language
        from voice.stt import _strip_wake
        audio = decode_audio(io.BytesIO(data), sampling_rate=16000)
        if len(audio) <= 16000 * 0.3:
            return ""
        if strip_wake:   # a command: Whisper, or Gemini when it looks like Tamil (as on the PC)
            return language.hear(stt, audio)
        text = stt.transcribe(audio, strip_wake=False)   # waiting for her name: local only (room talk never leaves the PC)
        hear.last_confidence = stt.last_confidence
        return text
    return hear


def make_voice(voice, rate="+8%"):
    """Text -> (Jarvis's voice as mp3 bytes, the words spoken). Same voice as the PC; in Tamil mode the reply is
    translated and read by the Tamil voice, like on the PC."""
    import asyncio
    import edge_tts
    from voice import language

    def speak(text):
        said, use = text, voice

        async def run():
            out = bytearray()
            async for chunk in edge_tts.Communicate(said, use, rate=rate).stream():
                if chunk["type"] == "audio":
                    out += chunk["data"]
            return bytes(out)
        return asyncio.run(run()), said
    return speak


def screenshot_jpeg(max_width=1280):
    """The main screen, small enough for a phone over mobile data."""
    import mss
    from PIL import Image
    with mss.mss() as s:
        shot = s.grab(s.monitors[1])
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


MANIFEST = json.dumps({
    "name": NAME, "short_name": NAME, "start_url": "/", "display": "standalone",
    "background_color": "#0a030a", "theme_color": "#0a030a",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
}).encode()
ICON = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="#0a030a"/>
<circle cx="50" cy="50" r="34" fill="none" stroke="#ff4da6" stroke-width="6"/><circle cx="50" cy="50" r="14" fill="#ff4da6"/></svg>"""
