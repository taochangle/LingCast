import unittest
import numpy as np
from audio_windows import AudioWindows

class Tests(unittest.TestCase):
    def test_no_duplicate_or_lost_audio_with_irregular_chunks(self):
        samples = np.arange(24000*3+927, dtype=np.float32)
        windows = AudioWindows(24000)
        output = []
        for chunk in np.array_split(samples, 13):
            output.extend(windows.feed(chunk))
        output.extend(windows.feed(np.empty(0), final=True))
        np.testing.assert_array_equal(np.concatenate([row[0] for row in output]), samples)
        self.assertEqual(output[0][2], 0)
        self.assertEqual(output[1][2], 4)
        np.testing.assert_array_equal(output[1][1][:3840], samples[24000-3840:24000])

    def test_first_window_emits_before_end_and_waits_for_context(self):
        windows = AudioWindows(24000)
        self.assertEqual(windows.feed(np.zeros(24000)), [])
        output = windows.feed(np.zeros(3840))
        self.assertEqual(len(output), 1)
        self.assertEqual(len(output[0][0]), 24000)
        self.assertEqual(len(output[0][1]), 27840)
        tail = windows.feed(np.empty(0), final=True)
        self.assertEqual(len(tail[0][0]), 3840)

    def test_subframe_tail_keeps_original_audio_but_has_a_render_frame(self):
        windows = AudioWindows(24000)
        core, render, left = windows.feed(np.ones(5), final=True)[0]
        self.assertEqual(len(core), 5)
        self.assertEqual(len(render), 960)
        self.assertEqual(left, 0)

if __name__ == '__main__':
    unittest.main()
