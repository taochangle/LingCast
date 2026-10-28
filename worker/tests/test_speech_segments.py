import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from ai.speech_segments import pause_boundaries, split_speech
from ai.resident_client import ResidentRenderer, CompletedClip


class SpeechSegmentsTests(unittest.TestCase):
    def test_no_midword_fallback(self):
        self.assertEqual(pause_boundaries(7, [2.7, 5.4]), [0, 2.7, 7])
        self.assertEqual(pause_boundaries(8, []), [0, 8])
        with self.assertRaises(ValueError):
            pause_boundaries(21, [])

    def test_audio_samples_preserved_at_pause_cuts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.wav'
            rate = 16000
            samples = bytearray()
            for i in range(rate * 9):
                value = int(12000 * math.sin(2*math.pi*220*i/rate)) if (i/rate) % 3 < 2.7 else 0
                samples.extend(struct.pack('<h', value))
            with wave.open(str(source), 'wb') as wav:
                wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                wav.writeframes(samples)
            parts = split_speech(source, root)
            self.assertEqual(len(parts), 3)
            joined = bytearray()
            for path, start, end in parts:
                self.assertLessEqual(end-start, 4.5)
                with wave.open(str(path), 'rb') as wav:
                    joined.extend(wav.readframes(wav.getnframes()))
            self.assertEqual(joined, samples)

    def test_first_clip_yielded_before_later_render(self):
        renderer = ResidentRenderer.__new__(ResidentRenderer)
        renderer.tts_url = 'tts'
        renderer.signed = lambda *args: 'signed'
        renderer.speech = Mock(return_value={'s3_key': 'tts/narration/test/full.wav',
                                          'metrics': {'audio_seconds': 6}})
        renderer.storage = Mock()
        renderer.render = Mock(side_effect=lambda *args, **kwargs:
                               CompletedClip(Path('clip.mp4'), 'clip', {'total_seconds': 1}))
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'read_bytes', return_value=b'pcm'), patch('ai.speech_segments.split_speech',
                return_value=[(Path('a.wav'), 0, 3), (Path('b.wav'), 3, 6)]):
            stream = renderer.render_narration('完整的一段话', 'base', temp, 'test', 123)
            first = next(stream)
            self.assertEqual(first.metrics['narration']['index'], 1)
            self.assertEqual(renderer.render.call_count, 1)
            second = next(stream)
            self.assertEqual(second.metrics['narration']['index'], 2)
            self.assertEqual(renderer.speech.call_count, 1)
            with self.assertRaises(StopIteration):
                next(stream)
