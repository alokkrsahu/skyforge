"""
Real-time audio beat detector.

Reads from the default input device (microphone or system audio loopback),
computes RMS energy per chunk, and fires a beat when energy exceeds an
adaptive threshold. The beat_energy value (0..1) decays between beats so the
LED brightness has a smooth pulse shape.

macOS note: Terminal needs Microphone permission.
  System Settings → Privacy & Security → Microphone → enable Terminal / iTerm.

To use system audio instead of mic (no physical mic required):
  Install BlackHole (free): https://existential.audio/blackhole/
  Set it as the default input device before running the show.
"""
import math
import threading
import time
from collections import deque

try:
    import numpy as np
    import sounddevice as sd
    _AUDIO_OK = True
except ImportError:
    _AUDIO_OK = False

# Audio capture settings
_RATE      = 44100
_CHUNK     = 1024          # ~23 ms per chunk → ~43 callbacks/sec
_HISTORY   = 50            # ~1.2 s of energy history for adaptive threshold
_THRESHOLD = 1.5           # beat if rms > THRESHOLD × mean(history)
_DECAY     = 0.80          # beat_energy multiplied by this each audio chunk (~0.3s to 10%)


class BeatDetector:
    """Thread-safe beat energy source. Start once; read beat_energy from any thread."""

    def __init__(self):
        self._energy   = 0.0
        self._lock     = threading.Lock()
        self._stop     = threading.Event()
        self._history  = deque(maxlen=_HISTORY)
        self._thread   = None
        self.available = _AUDIO_OK

    @property
    def beat_energy(self) -> float:
        """Current beat energy in [0, 1]. Peaks on beat, decays between beats."""
        with self._lock:
            return self._energy

    def start(self) -> None:
        if not _AUDIO_OK:
            print("[beat] sounddevice / numpy not available — audio sync disabled")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="beat-detector")
        self._thread.start()
        print("[beat] Audio beat detector started (listening on default input device)")

    def stop(self) -> None:
        self._stop.set()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _callback(self, indata, frames, time_info, status):
        rms = float(math.sqrt(max(1e-12, float(np.mean(indata.astype(np.float32) ** 2)))))
        self._history.append(rms)

        with self._lock:
            if len(self._history) >= 10:
                avg = sum(self._history) / len(self._history)
                if avg > 1e-8 and rms > _THRESHOLD * avg:
                    self._energy = 1.0          # beat detected
                else:
                    self._energy *= _DECAY      # decay between beats
            else:
                self._energy *= _DECAY

    def _run(self):
        try:
            with sd.InputStream(
                samplerate=_RATE,
                channels=1,
                blocksize=_CHUNK,
                dtype='int16',
                callback=self._callback,
            ):
                while not self._stop.is_set():
                    time.sleep(0.05)
        except Exception as exc:
            print(f"[beat] Audio stream error: {exc}")
            print("[beat]   → Check microphone permissions (System Settings → Privacy → Microphone)")
