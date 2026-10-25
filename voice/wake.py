import openwakeword
import openwakeword.utils
from openwakeword.model import Model


class WakeWord:
    """Listens for "Hey Jarvis" using openWakeWord's pre-trained model."""

    def __init__(self, threshold):
        try:
            openwakeword.utils.download_models(model_names=["hey_jarvis"])
        except TypeError:  # older versions take no arguments
            openwakeword.utils.download_models()
        # onnx works on Windows (tflite is Linux-only).
        # vad_threshold: only count it when real human speech is present, so a lower
        # wake threshold doesn't cause random triggers from fans, music or keyboard noise.
        self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx", vad_threshold=0.5)
        self.threshold = threshold

    def score(self, frame):
        return max(self.model.predict(frame).values())

    def detect(self, frame):
        return self.score(frame) >= self.threshold

    def reset(self):
        self.model.reset()
