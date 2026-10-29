"""Continuous PCM windows with MuseTalk context; core audio never repeats."""
import numpy as np

class AudioWindows:
    def __init__(self, rate):
        if rate % 25:
            raise ValueError('sample rate must align with 25fps')
        self.rate = rate
        self.core = rate  # One second per window.
        self.context = rate // 25 * 4  # 160ms lookbehind/lookahead.
        self.pending = np.empty(0, dtype=np.float32)
        self.left = np.empty(0, dtype=np.float32)

    def feed(self, pcm, final=False):
        self.pending = np.concatenate((self.pending, pcm))
        output = []
        while len(self.pending) >= self.core+self.context or (final and len(self.pending)):
            length = min(self.core, len(self.pending))
            core = self.pending[:length].copy()
            window = np.concatenate((self.left, self.pending[:length+self.context]))
            # A sub-frame tail still needs one image; pad only the render context.
            frame = self.rate//25
            required = len(self.left)+((length+frame-1)//frame)*frame
            if len(window) < required:
                window = np.pad(window, (0, required-len(window)))
            output.append((core, window, len(self.left)//(self.rate//25)))
            self.left = np.concatenate((self.left, core))[-self.context:]
            self.pending = self.pending[length:]
        return output
