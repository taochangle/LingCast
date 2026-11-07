import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from streaming.ffmpeg_pipe import FFmpegPipe, FFmpegPipeClosedError, _SocketAudio


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg and ffprobe are required')
class WindowsAudioPipeTests(unittest.TestCase):
    def test_full_audio_queue_times_out_instead_of_growing_without_limit(self):
        audio = _SocketAudio()
        self.addCleanup(audio.abort)
        for _ in range(64):
            audio.items.put_nowait(b'a')
        with self.assertRaises(FFmpegPipeClosedError):
            audio.put(b'b', lambda: None, timeout=0.05)

    def test_stop_cleans_up_after_ffmpeg_has_exited(self):
        with tempfile.TemporaryDirectory() as directory, patch('streaming.ffmpeg_pipe._use_windows_audio', return_value=True):
            pipe = FFmpegPipe('dead', 64, 64, 25, rtmp_url=str(Path(directory) / 'dead.flv'), log_path=Path(directory) / 'ffmpeg.log')
            pipe.start()
            process = pipe.proc
            process.kill()
            process.wait(timeout=5)
            pipe.stop(timeout=3)
            self.assertIsNone(pipe.proc)
            self.assertIsNone(pipe._socket_audio)

    def test_stop_without_frames_terminates_process(self):
        with tempfile.TemporaryDirectory() as directory, patch('streaming.ffmpeg_pipe._use_windows_audio', return_value=True):
            pipe = FFmpegPipe('empty', 64, 64, 25, rtmp_url=str(Path(directory) / 'empty.flv'), log_path=Path(directory) / 'ffmpeg.log')
            pipe.start()
            process = pipe.proc
            start = time.monotonic()
            pipe.stop(timeout=3)
            self.assertLess(time.monotonic()-start, 8)
            self.assertIsNotNone(process.poll())

    def test_windows_audio_and_video_are_written_to_local_file(self):
        self._check_audio_video(use_socket=True)

    @unittest.skipIf(__import__('sys').platform == 'win32', 'POSIX audio uses file descriptors')
    def test_posix_audio_and_video_are_written_to_local_file(self):
        self._check_audio_video(use_socket=False)

    def _check_audio_video(self, use_socket):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'test.flv'
            pipe = FFmpegPipe('test', 64, 64, 25, rtmp_url=str(output), log_path=Path(directory) / 'ffmpeg.log', per_frame_audio=True)
            errors = []

            def write():
                try:
                    pipe.start()
                    frame = np.zeros((64, 64, 3), dtype=np.uint8)
                    pcm = (np.sin(np.arange(32000) * (2*np.pi*440/16000)) * 12000).astype('<i2').tobytes()
                    for index in range(50):
                        pipe.write_audio(pcm[index*1280:(index+1)*1280])
                        pipe.write_frame(frame)
                    self.assertEqual(pipe.stop(timeout=15), 0)
                except BaseException as error:
                    errors.append(error)

            with patch('streaming.ffmpeg_pipe._use_windows_audio', return_value=use_socket):
                thread = threading.Thread(target=write, daemon=True)
                thread.start()
                thread.join(30)
            self.assertFalse(thread.is_alive(), 'audio and video transport deadlocked')
            if errors:
                raise errors[0]
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type:format=duration', '-of', 'json', str(output)], capture_output=True, text=True, check=True)
            metadata = json.loads(result.stdout)
            kinds = {item['codec_type'] for item in metadata['streams']}
            self.assertEqual(kinds, {'audio', 'video'})
            self.assertTrue(1.8 <= float(metadata['format']['duration']) <= 2.3)
            decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:a:0', '-f', 's16le', '-ac', '1', '-ar', '16000', '-'], capture_output=True, check=True)
            self.assertGreater(np.abs(np.frombuffer(decoded.stdout, dtype='<i2').astype(np.int32)).max(), 1000)


if __name__ == '__main__':
    unittest.main()
