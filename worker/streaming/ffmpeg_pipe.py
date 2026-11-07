"""FFmpeg subprocess pipe: raw BGR frames + 16k PCM audio -> RTMP/FLV.

The worker renders lip-synced frames in memory (Wav2Lip) and must push them
to SRS without writing an MP4. We start a single ffmpeg process with two
inputs:

  - video: `-f rawvideo -pix_fmt bgr24 -s WxH -r FPS -i pipe:0`
  - audio: 16kHz mono PCM via `/dev/fd/<fd>` on POSIX, or a
    loopback TCP input on Windows.

On POSIX the audio read descriptor is inherited by FFmpeg. On Windows a
bounded queue lets the producer continue until FFmpeg opens its second input.
Two rules avoid pipe deadlocks:

  - ffmpeg waits for the first packet of EVERY input before consuming, so the
    first audio slice must be written before the first video frame.
  - a whole chunk of audio written at once overflows ffmpeg's pre-video input
    buffering and blocks; audio must be interleaved with video in small slices
    (the stream worker writes half-second slices every `fps/2` frames).

The muxer assigns timestamps from consumed frames/samples, so interleaving
order does not affect A/V sync.

Pacing: the stream worker's **watchdog writer thread** pushes exactly `fps`
frames/second (and the matching per-frame audio slices) and the video input
keeps ffmpeg `-re`, so ffmpeg consumes in real time and the muxer output is
smooth even if the writer's wall-clock sleep jitters. The earlier "lag -> EOF"
failure mode is gone because the watchdog falls back to base-animation frames
the moment the ready queue is empty, so ffmpeg's video input never goes quiet.
"""

import logging
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_SAMPLE_RATE = 16000
_AUDIO_WRITE_CHUNK = 4096


class FFmpegPipeClosedError(OSError):
    """Raised when writing to a pipe whose ffmpeg process has exited."""


def _use_windows_audio():
    return sys.platform == 'win32'


class _SocketAudio:
    """Buffer audio until FFmpeg opens its second input on Windows."""

    def __init__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(0.25)
        self.url = f'tcp://127.0.0.1:{self.listener.getsockname()[1]}'
        self.items = queue.Queue(maxsize=64)
        self.failure = None
        self.closed = threading.Event()
        self.connection = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            while not self.closed.is_set():
                try:
                    self.connection, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            if self.connection is None:
                return
            self.connection.settimeout(5)
            while not self.closed.is_set():
                item = self.items.get()
                if item is None:
                    break
                self.connection.sendall(item)
        except OSError as error:
            if not self.closed.is_set():
                self.failure = error
        finally:
            if self.connection is not None:
                self.connection.close()
            self.listener.close()

    def put(self, item, check_alive, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            check_alive()
            if self.failure:
                raise FFmpegPipeClosedError('Windows audio transport failed') from self.failure
            if self.closed.is_set():
                raise FFmpegPipeClosedError('Windows audio transport closed')
            try:
                self.items.put(item, timeout=min(0.25, max(0, deadline-time.monotonic())))
                return
            except queue.Full:
                if time.monotonic() >= deadline:
                    raise FFmpegPipeClosedError('Windows audio transport timed out')

    def close(self, check_alive, timeout):
        try:
            self.put(None, check_alive, timeout=timeout)
            if self.thread:
                self.thread.join(timeout)
        finally:
            self.abort()

    def abort(self):
        self.closed.set()
        try:
            self.items.put_nowait(None)
        except queue.Full:
            pass
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.listener.close()
        if self.thread:
            self.thread.join(1)


class FFmpegPipe:
    """A single ffmpeg process encoding BGR24 frames + s16le PCM to an
    RTMP/FLV destination (SRS or a local file for testing)."""

    def __init__(
        self,
        stream_id: str,
        width: int,
        height: int,
        fps: float,
        rtmp_url: str | None = None,
        ffmpeg_bin: str = "ffmpeg",
        log_path: Path | None = None,
        per_frame_audio: bool = False,
    ):
        self.per_frame_audio = per_frame_audio
        self.stream_id = stream_id
        self.width = width
        self.height = height
        self.fps = float(fps)
        self.rtmp_url = rtmp_url or os.environ.get(
            "STREAM_RTMP_URL", "rtmp://localhost:1935/live/{stream_id}"
        ).format(stream_id=stream_id)
        self.ffmpeg_bin = ffmpeg_bin
        default_log = Path(tempfile.gettempdir()) / f'ffmpeg_{stream_id}.log' if _use_windows_audio() else Path(f'/tmp/ffmpeg_{stream_id}.log')
        self.log_path = log_path or Path(os.environ.get('STREAM_FFMPEG_LOG', str(default_log)))
        self.proc: subprocess.Popen | None = None
        self._audio_fd: int | None = None
        self._socket_audio: _SocketAudio | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Launch FFmpeg with video on stdin and a separate audio input."""
        if self.proc is not None:
            return

        use_socket = _use_windows_audio()
        audio_r, audio_w = (None, None) if use_socket else os.pipe()
        audio_socket = _SocketAudio() if use_socket else None
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-loglevel", "warning",
            "-re",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "pipe:0",
            *(["-probesize", "32", "-analyzeduration", "0"] if self.per_frame_audio else []),
            "-f", "s16le",
            "-ar", str(AUDIO_SAMPLE_RATE),
            "-ac", "1",
            "-i", audio_socket.url if audio_socket else f"/dev/fd/{audio_r}",
        ]
        cmd += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-g", str(max(2, int(self.fps * 2))),
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-flush_packets", "1",
            "-f", "flv",
            self.rtmp_url,
        ]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(self.log_path, "wb")
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log_file,
                **({} if use_socket else {'pass_fds': (audio_r,)}),
            )
        except BaseException:
            if audio_socket:
                audio_socket.listener.close()
            else:
                os.close(audio_r)
                os.close(audio_w)
            raise
        finally:
            log_file.close()
        if audio_socket:
            self._socket_audio = audio_socket
            audio_socket.start()
        else:
            os.close(audio_r)
            self._audio_fd = audio_w
        logger.info(
            "ffmpeg pipe started for stream %s -> %s (log %s)",
            self.stream_id,
            self.rtmp_url,
            self.log_path,
        )

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #
    def _check_alive(self) -> None:
        if self.proc is None:
            raise FFmpegPipeClosedError(
                f"ffmpeg pipe for stream {self.stream_id} was never started"
            )
        code = self.proc.poll()
        if code is not None:
            raise FFmpegPipeClosedError(
                f"ffmpeg exited with code {code} for stream {self.stream_id} "
                f"(see {self.log_path})"
            )

    def write_frame(self, bgr_frame) -> None:
        """Write one BGR24 frame (OpenCV ndarray, HxWx3 uint8)."""
        self._check_alive()
        try:
            self.proc.stdin.write(bgr_frame.tobytes())  # type: ignore[union-attr]
        except (BrokenPipeError, OSError) as exc:
            raise FFmpegPipeClosedError(
                f"video pipe closed for stream {self.stream_id} "
                f"(ffmpeg log: {self.log_path})"
            ) from exc

    def write_audio(self, pcm16_bytes: bytes) -> None:
        """Write mono 16kHz s16le PCM in small pieces to avoid pipe deadlocks."""
        self._check_alive()
        if self._audio_fd is None and self._socket_audio is None:
            raise FFmpegPipeClosedError(
                f"audio pipe for stream {self.stream_id} was never started"
            )
        try:
            view = memoryview(pcm16_bytes)
            pos = 0
            while pos < len(view):
                if self._socket_audio is not None:
                    chunk = bytes(view[pos : pos + _AUDIO_WRITE_CHUNK])
                    self._socket_audio.put(chunk, self._check_alive)
                    pos += len(chunk)
                    continue
                written = os.write(
                    self._audio_fd, view[pos : pos + _AUDIO_WRITE_CHUNK]
                )
                if written <= 0:
                    raise OSError("ffmpeg audio pipe closed early")
                pos += written
        except (BrokenPipeError, OSError) as exc:
            raise FFmpegPipeClosedError(
                f"audio pipe closed for stream {self.stream_id} "
                f"(ffmpeg log: {self.log_path})"
            ) from exc

    # ------------------------------------------------------------------ #
    # Teardown
    # ------------------------------------------------------------------ #
    def stop(self, timeout: float = 10.0) -> int:
        """Close both inputs and wait for ffmpeg to finish; returns exit code."""
        code = 1
        try:
            if self.proc is not None and self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except OSError:
                    pass
            if self._socket_audio is not None:
                if self.proc is None or self.proc.poll() is not None:
                    self._socket_audio.abort()
                else:
                    try:
                        self._socket_audio.close(self._check_alive, timeout)
                    except FFmpegPipeClosedError:
                        self._socket_audio.abort()
            if self._audio_fd is not None:
                os.close(self._audio_fd)
            if self.proc is not None:
                try:
                    code = self.proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    code = self.proc.wait(timeout=timeout)
                if code != 0:
                    logger.error(
                        "ffmpeg exited with %s for stream %s (see %s)",
                        code,
                        self.stream_id,
                        self.log_path,
                    )
        finally:
            self.proc = None
            self._audio_fd = None
            self._socket_audio = None
        return code
