import glob
import os
import re
import site
import sys


def _add_nvidia_dlls():
    """On Windows, Whisper's GPU mode needs NVIDIA's cuBLAS + cuDNN DLLs.
    `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` puts them inside the venv;
    this tells Windows where to find them."""
    if sys.platform != "win32":
        return
    roots = site.getsitepackages() + [site.getusersitepackages()]
    for root in roots:
        for bin_dir in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(bin_dir)
            except OSError:
                pass
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


_add_nvidia_dlls()

from faster_whisper import WhisperModel  # noqa: E402

# Whisper doesn't know the name "Keena": it wrote "Kina", "Kena", "Hina", "Tina", "Hekina" (measured, 4 voices).
# The prompt nudges it toward the right words; names it wouldn't know otherwise ("Ravi" was heard as "money").
HINT = ("Hey Keena. Keena, open Chrome. Set the volume to 40. Play on YouTube. Thanks Keena.")
import config  # noqa: E402,F401  (loads .env, for KNOWN_EMAIL_DOMAINS below)
# his email domains (.env KNOWN_EMAIL_DOMAINS): "arjun at pixelcraftstudio.com" was heard as "Pixelcraftstudi0" / "Pxlstudio"
# names Whisper wouldn't know (people, companies): .env HEAR_NAMES, comma separated ("Ravi" was heard as "money")
HINT += "".join(f" {n.strip()}." for n in os.getenv("HEAR_NAMES", "").split(",") if n.strip())
HINT += "".join(f" Email: name@{d}." for d in os.getenv("KNOWN_EMAIL_DOMAINS", "").split(",") if d.strip())

# Her name as Whisper writes it. STRONG: rare spellings, count anywhere. WEAK: real names (Hina, Tina, Gina), count
# only right after "hey" or as the first word. GLUED: "Hey Keena" run together.
NAME_STRONG = r"keena|keenah|keena's|kina|kinah|kena|keina|qina|kiina"
NAME_WEAK = r"hina|tina|gina|heena|keeno"
NAME_GLUED = r"hekina|heikina|heykina|hikina|heykeena|hikeena|heykena"

# Phrases Whisper "hears" in silence/noise (it was trained on lots of YouTube videos)
HALLUCINATION = re.compile(r"^\W*(?:(?:thanks?|thank you) (?:so much )?for watching|(?:please )?subscribe(?: to (?:my|the|our) channel)?.*|"
                           r"subtitles? by .*|captions? by .*|see you (?:next time|in the next video)|"
                           r"like and subscribe|i'?ll see you (?:next time|in the next one))\W*$", re.I)
# Extra noise words only dropped from LIVE (partial) text, where they're almost always noise
PARTIAL_NOISE = re.compile(r"^\W*(?:you|thank you|thanks|bye|okay|so|uh|um|hmm|\.+)\W*$", re.I)


def _clean_hallucination(text, partial=False):
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    keep = [p for p in parts if not HALLUCINATION.match(p) and not (partial and PARTIAL_NOISE.match(p))]
    return " ".join(keep).strip()


# Leftover wake word at the start of a command ("Hey Jarvis, set volume...") is stripped.
WAKE_PREFIX = re.compile(rf"^\s*(?:(?:hey|hi|ok|okay)[\s,]+(?:{NAME_STRONG}|{NAME_WEAK})|{NAME_STRONG}|{NAME_GLUED}|(?:{NAME_WEAK})(?=\s*,))\b[\s,.!?]*", re.I)


class SpeechToText:
    def __init__(self, size, device):
        import threading
        self.size = size
        self.lock = threading.Lock()
        self._load(device)

    def partial(self, audio):
        """Quick live transcript while you're still talking. Skips (returns None) if Whisper is busy."""
        if not self.lock.acquire(blocking=False):
            return None
        try:
            segments, _ = self.model.transcribe(audio, beam_size=1, language="en", vad_filter=True,
                                                initial_prompt=HINT, without_timestamps=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            if text.strip(" .!?").lower() == HINT.strip(" .").lower():
                return ""
            return _clean_hallucination(text, partial=True)
        except Exception:
            return None
        finally:
            self.lock.release()

    def _load(self, device):
        compute = "int8_float16" if device == "cuda" else "int8"  # int8 on GPU = less VRAM
        self.device = device
        self.model = WhisperModel(self.size, device=device, compute_type=compute)
        self._warm_up()

    def _warm_up(self):
        """The first transcription is slow (GPU/CPU warm-up). Do it now at startup, not on your first command."""
        import numpy as np
        try:
            noise = (np.random.default_rng(0).standard_normal(16000) * 0.01).astype(np.float32)
            list(self.model.transcribe(noise, beam_size=1, language="en", vad_filter=False)[0])
            # and the voice-activity filter real commands use: it loads its own model on first use (+0.24 s measured)
            list(self.model.transcribe(noise, beam_size=1, language="en", vad_filter=True, initial_prompt=HINT)[0])
        except Exception as e:
            if self.device == "cuda":
                print(f"[Whisper GPU failed at startup: {e}] -> using CPU")
                self.device = "cpu"
                self.model = WhisperModel(self.size, device="cpu", compute_type="int8")

    def _run(self, audio):
        segments, _ = self.model.transcribe(
            audio, beam_size=1, language="en", vad_filter=True, initial_prompt=HINT,
        )
        segs = list(segments)
        # how sure Whisper was (mean log-probability per token, length-weighted): Tamil speech forced through this
        # English-only model comes out as confident-sounding nonsense with a LOW score ("Noodle Sami Klandra")
        words = sum(max(1, len(s.text.split())) for s in segs)
        self.last_confidence = (sum(s.avg_logprob * max(1, len(s.text.split())) for s in segs) / words) if segs else 0.0
        return " ".join(s.text.strip() for s in segs).strip()

    def transcribe(self, audio, strip_wake=True):
        try:
            with self.lock:
                text = self._run(audio)
        except RuntimeError as e:
            # GPU libraries missing or broken: don't crash, fall back to the CPU and keep going
            if self.device != "cuda":
                raise
            print(f"\n[Whisper GPU failed: {e}]")
            print("[Switching speech-to-text to CPU. See README 'GPU for Whisper' to fix.]\n")
            self._load("cpu")
            text = self._run(audio)
        if text.strip(" .!?").lower() == HINT.strip(" .").lower():  # rare: whisper echoes the hint on silence
            return ""
        text = _clean_hallucination(text)
        if not strip_wake:
            return text
        return _strip_wake(text)


# "Keena" anywhere (rare spellings), "hey" + any spelling, or a real name (Tina...) as the first word with a comma
WAKE_IN_TEXT = re.compile(
    rf"(?:\b(?:hey|hi|ok|okay|yo)[\s,]+(?:{NAME_STRONG}|{NAME_WEAK})\b|\b(?:{NAME_GLUED}|{NAME_STRONG})\b"
    rf"|^\W*(?:{NAME_WEAK})(?=\s*,))[\s,.!?]*", re.I)


# "Hey Keena" said again mid-command is a repeated wake-up, never part of the sentence
GREETING_WAKE = re.compile(rf"(?:\b(?:hey|hi|ok|okay|yo)[\s,]+(?:{NAME_STRONG}|{NAME_WEAK})|\b(?:{NAME_GLUED}))\b[\s,.!?]*", re.I)
TRAILING_VOCATIVE = re.compile(rf"(?:,|(?<=[.!?]))\s*(?:{NAME_STRONG})[\s.!?]*$", re.I)
ONLY_WAKE = re.compile(rf"^[\s,.!?]*(?:(?:(?:hey|hi|ok|okay|yo)?[\s,]*(?:{NAME_STRONG}|{NAME_WEAK})|{NAME_GLUED})[\s,.!?]*)*$", re.I)


def _strip_wake(text):
    """Remove the wake word, keep the sentence. A bare "Jarvis" is only dropped at the very start:
    "Why did I name you Jarvis?" must keep its Jarvis (it used to come out as "why did I name you")."""
    text = WAKE_PREFIX.sub("", text, count=1)
    text = GREETING_WAKE.sub(" ", text)   # "open Last of Us? Hey Jarvis." -> "open Last of Us?"
    # talking TO him at the end ("open Chrome, Jarvis." / "...thanks. Jarvis.") isn't part of the command;
    # "I named you Jarvis." (no comma) is
    text = TRAILING_VOCATIVE.sub("", text)
    if ONLY_WAKE.match(text):
        return ""
    return re.sub(r"\s+", " ", text).strip(" ,")


def find_wake(text):
    """If the transcript contains the wake word, return what was said around it (the command,
    possibly empty). Otherwise None. "Jarvis, open Chrome" -> "open Chrome"."""
    m = WAKE_IN_TEXT.search(text or "")
    if not m:
        return None
    # drop the wake word that woke it, plus any repeated "Hey Jarvis"; a later bare "Jarvis" stays
    rest = text[:m.start()] + " " + text[m.end():]
    return _strip_wake(rest).strip(" ,.!?")
