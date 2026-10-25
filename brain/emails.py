"""Spoken email addresses -> real ones.

Saying an address out loud always reaches Keena as words: "arjun at pixelcraftstudio dot com". It was saved like that
(no "@"), so nothing recognised it as an address, and Whisper heard the domain as "Pixelcraftstudi0" / "Pxlstudio".

- "x at y dot com" / "x at y.com" -> "x@y.com"
- a domain one or two letters off a domain he uses (KNOWN_EMAIL_DOMAINS in .env, addresses in his profile, the
  common mail providers) is corrected to it, and the correction is printed
"""
import os
import re
from difflib import SequenceMatcher

COMMON = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "live.com"]
SPOKEN = re.compile(r"\b([a-z0-9][\w.+-]*)\s+(?:at|@)\s+([a-z0-9][\w-]*(?:\s*(?:\.|\bdot\b)\s*[a-z]{2,}[\w-]*)+)\b", re.I)
WRITTEN = re.compile(r"\b([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)\b")


def known_domains(text=""):
    env = [d.strip().lower() for d in os.getenv("KNOWN_EMAIL_DOMAINS", "").split(",") if d.strip()]
    seen = [m[1].lower() for m in WRITTEN.finditer(text or "")]
    return list(dict.fromkeys(env + seen + COMMON))


def correct_domain(domain, known):
    domain = domain.lower().strip(".")
    if domain in known:
        return domain
    best = max(known, key=lambda k: SequenceMatcher(None, domain, k).ratio(), default=None)
    if best and SequenceMatcher(None, domain, best).ratio() >= 0.8:
        print(f"   [email: '{domain}' taken as '{best}', a domain he uses]")
        return best
    return domain


def normalize(text, known=None):
    """Every address in the text, spoken or written, in its real form ("arjun@pixelcraftstudio.com")."""
    if not text or ("@" not in text and not re.search(r"\bat\b", text, re.I)):
        return text
    known = known_domains(text) if known is None else known

    def spoken(m):
        domain = re.sub(r"\s*(?:\.|\bdot\b)\s*", ".", m[2], flags=re.I)
        return f"{m[1].lower()}@{correct_domain(domain, known)}"
    text = SPOKEN.sub(spoken, text)
    return WRITTEN.sub(lambda m: f"{m[1].lower()}@{correct_domain(m[2], known)}", text)
