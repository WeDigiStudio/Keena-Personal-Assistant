"""Keena understands Tamil, Tanglish and English, and always answers in English.

Hearing is local first: the PC's English Whisper (0.1 s). When it wasn't sure (Tamil forced through the English-only
model comes out as confident-sounding nonsense with a low score: "Noodle Sami Klandra" for "noodles samaikka
poren"), the same audio goes to Gemini (gemini-3.1-flash-lite, audio in, ~1.5-2 s), which returns English. The brain
then works exactly as for English: instant commands, guards, memory.

Measured (46 clips, 5 voices): Whisper's mean token log-probability was -0.66 or lower for every Tamil / Tanglish clip
and -0.75..-0.12 (median -0.32) for English. Below -0.6 -> Gemini: 16/16 Tamil, 1/30 English (still understood).

Speaking Tamil was tried (Tamil mode) and dropped: "it is not good". Room talk while waiting for her name
never goes to Gemini (free quota); only what he says TO her.
"""
import base64
import http.client
import json
import os
import re
import threading

from config import ASSISTANT_NAME, GEMINI_API_KEY, TAMIL_MODEL, USER_NAME

CONFIDENCE_GATE = -0.6
# names and titles to expect ("Dude song" came back as "Tute song"); more in .env HEAR_NAMES, comma separated
NAMES = ", ".join([USER_NAME, "Vijay", "Thalapathy", "Anirudh", "Dude", "Leo", "Maggi", "YouTube", "Gmail", "Amazon",
                   "Flipkart"]
                  + [n.strip() for n in os.getenv("HEAR_NAMES", "").split(",") if n.strip()])
# romanised Tamil Whisper writes when it half-hears Tanglish ("Vijay oda nalla padam suggest pannu")
TANGLISH = re.compile(r"\b(?:pannu|pannunga|panren|pannitu|venuma|venum|irukku|iruku|irukken|podu|padam|enna|epdi|eppadi|"
                      r"sollu|kelu|anuppu|naan|unakku|enakku|romba|seri|sari|da|di|machan|nalla|oda|ku)\b", re.I)
HEAR_PROMPT = (
    f"{USER_NAME} (in India) is talking to his AI companion {ASSISTANT_NAME} (Whisper may write her name as Kina, Kena, Tina). "
    "He may speak Tamil, English or both mixed (Tanglish). Return JSON {\"said\": exactly what he said, word for word, "
    "Tamil words in Tamil script and English words in English, \"english\": the same in natural English}. The English "
    "must keep EVERY detail: every app, site or place he names (YouTube, Amazon, Chrome, Gmail...), every number, name, "
    "song or movie title, and the exact action (volume is not a timer, open is not close, draft is not send). A command "
    "stays a command in the same words: 'YouTube-ல Anirudh பாட்டு போடு' -> 'play Anirudh songs on YouTube'; 'Volume-அ "
    "20-க்கு வை' -> 'set the volume to 20'. Keep his way of talking to her (questions stay questions). Drop her name if he "
    "only used it to call her. No clear speech -> both empty. Names he often says (spell them like this): "
    f"{NAMES}.")
_local = threading.local()


def wav_bytes(audio):
    """float32 16 kHz numpy audio -> WAV bytes (for Gemini)."""
    import io
    import wave
    import numpy as np
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def _conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = _local.conn = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=10)   # audio: usually 1.5-3 s, sometimes 5+
    return c


def understand(wav):
    """His Tamil / Tanglish / English speech (WAV bytes) -> English for the brain, "" if nothing clear, or None if
    Gemini isn't available (then Whisper's text is used)."""
    if not GEMINI_API_KEY:
        return None
    body = json.dumps({"systemInstruction": {"parts": [{"text": HEAR_PROMPT}]},
                       "contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": "audio/wav",
                                                                              "data": base64.b64encode(wav).decode()}}]}],
                       "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                            "thinkingConfig": {"thinkingLevel": "minimal"}}})
    for attempt in range(2):   # a kept-open connection can go stale: one fresh retry
        try:
            c = _conn()
            c.request("POST", f"/v1beta/models/{TAMIL_MODEL}:generateContent", body,
                      {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"})
            r = c.getresponse()
            data = json.loads(r.read())
            if r.status != 200:
                print(f"   [Tamil hearing: Gemini said {r.status}{' (free quota used up for now)' if r.status == 429 else ''}"
                      f", using Whisper's English]")
                return None
            out = json.loads("".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]) or "{}")
            if isinstance(out, list):   # it sometimes wraps the answer in a list (the mail line was lost to that)
                out = next((o for o in out if isinstance(o, dict)), {})
            said, english = (out.get("said") or "").strip(), (out.get("english") or "").strip()
            if said:
                print(f"   (understood: {said}  ->  {english})")
            return english
        except Exception as e:
            _local.conn = None
            if attempt:
                print(f"   [Tamil hearing failed ({e!r}), using Whisper's English]")
                return None


def warm():
    """Open the connection to Gemini at startup: the first Tamil sentence took 10.6 s on a cold connection."""
    if not GEMINI_API_KEY:
        return
    try:
        c = _conn()
        c.request("GET", f"/v1beta/models/{TAMIL_MODEL}", headers={"x-goog-api-key": GEMINI_API_KEY})
        c.getresponse().read()
    except Exception:
        _local.conn = None


def needs_gemini(text, confidence):
    """Whisper wasn't sure, heard nothing, or wrote romanised Tamil: probably Tamil / Tanglish."""
    return bool(GEMINI_API_KEY) and (not text or confidence < CONFIDENCE_GATE or bool(TANGLISH.search(text or "")))


def hear(stt, audio, strip_wake=True):
    """What he said to her, in English: Whisper (local, instant) or, when it looks like Tamil, Gemini."""
    from voice.stt import _strip_wake
    text = stt.transcribe(audio, strip_wake=strip_wake)
    if needs_gemini(text, getattr(stt, "last_confidence", 0.0)):
        english = understand(wav_bytes(audio))
        if english is not None:
            return _strip_wake(english) if strip_wake and english else english
    return text
