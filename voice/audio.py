import queue

import numpy as np
import sounddevice as sd

from config import SAMPLE_RATE, FRAME, MIC_DEVICE


def list_mics():
    """Print every input device with its number, and mark the one Jarvis will use."""
    default_in = sd.default.device[0]
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            api = sd.query_hostapis(d["hostapi"])["name"]
            mark = "  <- Windows default" if i == default_in else ""
            print(f"  [{i}] {d['name']}  ({api}){mark}")


class Mic:
    """Keeps the microphone open and hands out 80 ms chunks of 16 kHz audio."""

    def __init__(self):
        self.q = queue.Queue()
        self.stream = sd.InputStream(
            device=MIC_DEVICE,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status):
        self.q.put(indata[:, 0].copy())

    def start(self):
        self.stream.start()

    def read(self, timeout=None):
        """Next 80 ms frame. With a timeout, returns None if nothing arrived in time."""
        try:
            frame = self.q.get(timeout=timeout)
        except queue.Empty:
            return None
        self._track_noise(frame)
        return frame

    def _track_noise(self, frame):
        """Remember recent loudness so we know how loud the room is when nobody talks (fan, AC...)."""
        if not hasattr(self, "_levels"):
            from collections import deque
            self._levels = deque(maxlen=250)  # ~20 s
        self._levels.append(rms(frame))

    def noise_floor(self):
        """Background noise level: the quieter 20% of the last ~20 s."""
        levels = sorted(getattr(self, "_levels", []))
        if len(levels) < 25:
            return 0.0
        return levels[len(levels) // 5]

    def silence_level(self, configured):
        """Below this counts as silence: your setting, raised automatically if the room is noisy."""
        return max(configured, self.noise_floor() * 2.0 + 80)

    def drain(self):
        """Take everything buffered so far (to put it back later with push_front)."""
        frames = []
        while True:
            try:
                frames.append(self.q.get_nowait())
            except queue.Empty:
                return frames

    def push_front(self, frames):
        """Put saved frames back in front of anything that arrived since."""
        newer = self.drain()
        for f in frames + newer:
            self.q.put(f)

    def lag(self):
        """Seconds of audio waiting to be processed. Should stay near 0."""
        return self.q.qsize() * FRAME / SAMPLE_RATE

    def clear(self):
        """Drop buffered audio (e.g. Jarvis's own voice) so it isn't processed."""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def record_command(self, silence_rms, silence_seconds, max_seconds, wait_seconds, on_frame=None):
        """Record until you stop talking. Returns float32 audio, or None if you said nothing."""
        silence_rms = self.silence_level(silence_rms)  # adapts to fans/AC so recording still ends
        self.last_threshold = silence_rms
        frame_sec = FRAME / SAMPLE_RATE
        max_frames = int(max_seconds / frame_sec)
        quiet_needed = int(silence_seconds / frame_sec)
        wait_frames = int(wait_seconds / frame_sec)

        long_talk = int(4 / frame_sec)                  # after 4 s of talking...
        quiet_long = max(quiet_needed, int(1.4 / frame_sec))  # ...allow 1.4 s thinking pauses
        frames, quiet, spoke, talking = [], 0, False, 0
        for i in range(max_frames):
            f = self.read()
            frames.append(f)
            level = rms(f)
            if on_frame:
                on_frame(frames, level, spoke or level > silence_rms)
            if level > silence_rms:
                spoke, quiet = True, 0
                talking += 1
            else:
                quiet += 1
                needed = quiet_long if talking >= long_talk else quiet_needed
                if spoke and quiet >= needed:
                    break
                if not spoke and i >= wait_frames:
                    return None
        if not spoke:
            return None
        return np.concatenate(frames).astype(np.float32) / 32768.0


def rms(frame):
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def chime(freq=880, duration=0.12, volume=0.25):
    """Short soft beep. Wake = high note, confirm prompt = lower note."""
    sr = 44100
    t = np.linspace(0, duration, int(sr * duration), False)
    tone = np.sin(2 * np.pi * freq * t) * volume
    fade = np.minimum(1, np.minimum(t, duration - t) / 0.02)  # 20 ms fade in/out, no click
    sd.play((tone * fade).astype(np.float32), sr)
    sd.wait()
