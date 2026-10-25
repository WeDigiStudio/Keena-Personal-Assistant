import asyncio
import os
import queue
import re
import tempfile
import threading
import time
import uuid

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import edge_tts  # noqa: E402
import pygame  # noqa: E402



class TextToSpeech:
    """Speaks sentence by sentence: sentence 1 starts playing while sentence 2 is still
    being generated, so you hear Jarvis sooner on longer replies."""

    def __init__(self, voice, rate="+8%"):
        self.voice = voice
        self.rate = rate          # slightly brisk, sounds more like Jarvis
        self.first_audio = 0.0    # seconds until the first sound, for the timing line
        self.on_play = None       # on_play(sentence): called the moment each sentence starts playing
        pygame.mixer.init()

    FILLERS = ["Let me think.", "One sec.", "Give me a second.", "Hmm, let me check.", "Just a moment."]

    def prepare_fillers(self):
        """Record the filler phrases once, in the background, so they play instantly later."""
        def work():
            ready = []
            for phrase in self.FILLERS:
                try:
                    ready.append(self._synth(phrase, self.voice))
                except Exception:
                    pass
            self.filler_files = ready
        self.filler_files = []
        threading.Thread(target=work, daemon=True).start()

    def filler(self):
        """Start a short 'let me think' right away (doesn't wait for it to finish)."""
        import random
        files = getattr(self, "filler_files", [])
        if not files or pygame.mixer.music.get_busy():
            return
        try:
            pygame.mixer.music.load(random.choice(files))
            pygame.mixer.music.play()
        except pygame.error:
            pass

    def _generate(self, text):
        return self._synth(text, self.voice)

    def _synth(self, text, voice):
        # uuid, not the clock: two sentences generated at the same instant must never share a file
        path = os.path.join(tempfile.gettempdir(), f"jarvis_{uuid.uuid4().hex}.mp3")
        asyncio.run(edge_tts.Communicate(text, voice, rate=self.rate).save(path))
        return path

    def say(self, text):
        if not text:
            return
        speaker = self.stream()
        speaker.add(text)
        speaker.finish()

    def stream(self):
        """Speak text as it arrives: speaker.add(sentence) while the AI is still writing, then speaker.finish()."""
        return Speaker(self)


class Speaker:
    """Each piece of text is turned into audio in its own thread the moment it arrives (edge-tts is online,
    ~0.5-1 s each), and one player thread plays them strictly in order."""

    def __init__(self, tts):
        self.tts = tts
        self.t0 = time.time()
        self.first_play = None     # time.time() when the first audio started
        self.queue = queue.Queue()
        self.player = threading.Thread(target=self._play_all, daemon=True)
        self.player.start()

    def add(self, text):
        for piece in _pieces(text):
            if not re.search(r"\w", piece):
                continue   # "...?" alone made edge-tts fail: "No audio was received"
            slot = {"path": None, "ready": threading.Event(), "text": piece}
            threading.Thread(target=self._generate, args=(piece, slot), daemon=True).start()
            self.queue.put(slot)

    def finish(self):
        """Wait until everything added so far has been spoken."""
        self.queue.put(None)
        self.player.join()
        if self.first_play:
            self.tts.first_audio = self.first_play - self.t0

    def _generate(self, piece, slot):
        try:
            slot["path"] = self.tts._generate(piece)
        except Exception as e:  # no internet etc.
            print(f"Voice error: {e}")
        slot["ready"].set()

    def _play_all(self):
        while (slot := self.queue.get()) is not None:
            slot["ready"].wait()
            path = slot["path"]
            if not path:
                continue
            while pygame.mixer.music.get_busy():  # let a filler ("let me think") finish first
                time.sleep(0.02)
            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                self.first_play = self.first_play or time.time()
                if self.tts.on_play:   # the HUD shows the sentence she is saying NOW (it ran far ahead of her)
                    self.tts.on_play(slot["text"])
                while pygame.mixer.music.get_busy():
                    time.sleep(0.02)
                pygame.mixer.music.unload()
            except pygame.error as e:  # never crash Jarvis because one sentence failed to play
                print(f"Voice playback error: {e}")
            try:
                os.remove(path)
            except OSError:
                pass


def _pieces(text):
    """Sentences; a long one is cut at commas so the voice can start sooner."""
    out = []
    for sent in (x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x):
        while len(sent) > 110 and ", " in sent[40:]:
            cut = sent.index(", ", 40) + 1
            out.append(sent[:cut])
            sent = sent[cut:].strip()
        out.append(sent)
    return out
