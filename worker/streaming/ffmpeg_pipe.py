"""FFmpeg subprocess pipe: raw BGR frames + 16k PCM audio -> RTMP/FLV.

The worker renders lip-synced frames in memory (Wav2Lip) and must push them
to SRS without writing an MP4. We start a single ffmpeg process with two
inputs:

  - video: `-f rawvideo -pix_fmt bgr24 -s WxH -r FPS -i pipe:0`
  - audio: `-f s16le -ar 16000 -ac 1 -i /dev/fd/<fd>`

The audio input is passed through `pass_fds` as file descriptor 3 and ffmpeg
opens it via `/dev/fd/3` (works on both macOS and Linux). Two rules avoid pipe
deadlocks:

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
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_SAMPLE_RATE = 16000
_AUDIO_WRITE_CHUNK = 4096


class FFmpegPipeClosedError(OSError):
    """Raised when writing to a pipe whose ffmpeg process has exited."""


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
        self.log_path = log_path or Path(
            os.environ.get("STREAM_FFMPEG_LOG", f"/tmp/ffmpeg_{stream_id}.log")
        )
        self.proc: subprocess.Popen | None = None
        self._audio_fd: int | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Launch ffmpeg. Video arrives on stdin; audio on fd 3."""
        if self.proc is not None:
            return

        audio_r, audio_w = os.pipe()
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
            "-i", f"/dev/fd/{audio_r}",
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
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            pass_fds=(audio_r,),
        )
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
        if self._audio_fd is None:
            raise FFmpegPipeClosedError(
                f"audio pipe for stream {self.stream_id} was never started"
            )
        try:
            view = memoryview(pcm16_bytes)
            pos = 0
            while pos < len(view):
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
                self.proc.stdin.close()
            if self._audio_fd is not None:
                os.close(self._audio_fd)
            if self.proc is not None:
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
        return code
