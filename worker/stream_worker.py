"""Streaming worker: continuous per-avatar FFmpeg pipe with a watchdog writer.

One live session per avatar keeps a single ffmpeg process pushing
`rtmp://localhost:1935/live/avatar_<id>` to SRS. The pipe is NEVER closed and
the **watchdog writer thread** pushes exactly `fps` frames per second (24 by
default), so the frontend player never sees a gap and never buffers ("转圈"):

  - The writer owns the pacing. Every 1/24s it pops the next ready frame from
    the `Ready_Frames_Queue` (lip-synced frames produced by the inference
    thread) and writes the matching per-frame audio slice.
  - If the queue is empty (idle, or Wav2Lip is still producing the first
    batch), it immediately falls back to the next pre-processed `base_video.mp4`
    frame + a silent audio slice. The avatar blinks/moves silently; when the
    first talking batch lands it switches seamlessly back to speech.
  - The inference thread (async): Edge-TTS runs in the background; finished
    audio is lip-synced in small Wav2Lip batches (8 frames) and pushed to the
    queue as fast as possible (faster than real-time), so consecutive chunks
    splice with no idle gap at all.

Audio is written in aligned 0.5s slices (16kHz s16le mono = 16000 bytes), the
same interleaving pattern the pre-Watchdog pipeline used: the first 0.5s of a
talking segment is pre-buffered before its first frame, then one 0.5s slice is
written every `fps/2` frames. This gives the AAC encoder enough runway that
writer-thread jitter cannot cause underruns/clicks, while per-frame slices
could. Idle silence uses the same 0.5s slice cadence.

Face restoration (GFPGAN/CodeFormer) is intentionally OFF in the live pipeline:
it is ~1s/frame on Apple Silicon CoreML and cannot sustain 24fps.

Idle polish (2026-08): idle clips crossfade over ~0.4s instead of hard
cutting, and each idle frame gets a procedural breathing scale + tiny
vertical drift (pure CPU numpy/OpenCV, far inside the 24fps budget) so the
standing pose never reads as a frozen loop. Tune per-avatar via
liveSettings.idleFadeSeconds / idleMotion, or the IDLE_* env overrides.
"""

import json
import logging
import math
import os
import queue as queue_mod
import random
import requests
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import redis

from storage import S3Storage
import numpy as np
from ai.resident_client import ResidentRenderer, CompletedClip
from ai.host_director import HostDirector
from streaming.ffmpeg_pipe import AUDIO_SAMPLE_RATE, FFmpegPipeClosedError
from worker import (
    _check_required_env,
    _ensure_nltk_resources,
    _load_local_env,
    load_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("stream_worker")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Seconds of busy-wait at the end of each frame slot: time.sleep() alone
# overshoots by the GIL switch interval (~5ms by default), which drops the
# watchdog from 24fps to ~21.5fps and makes the player buffer. Sleeping the
# bulk then spinning the last few ms holds the deadline precisely.
PACING_BUSY_TAIL = 0.004
# If the watchdog goes this long without successfully writing a frame (e.g.
# ffmpeg's pipe deadlocks), treat the pipe as dead and rebuild the session.
STALL_RECOVERY_SECONDS = 8.0


def _read_video_frames(path):
    """Read every frame of a driving video (lazy import keeps startup light)."""
    from ai.lipsync_onnx import Wav2LipOnnxLipSync

    return Wav2LipOnnxLipSync._read_frames(path)


def parse_live_message(raw: str) -> tuple[str, str | None, str | None, int]:
    """Parse one live_queue entry into (text, tts_s3_key, base_video_s3_key, ts_ms).

    The queue carries plain-text sentences (the legacy/current producer) or
    JSON messages from the S3-shared-storage architecture:
        {"text": "...", "tts_s3_key": "tts/xxx.wav", "base_video_s3_key": "..."}
    The optional `base_video_s3_key` selects a specific action video to
    lip-sync THIS sentence against (agentic action selection); when absent the
    session falls back to the currently displayed idle clip.
    `ts_ms` is the backend enqueue wall-clock (ms) used to measure queue age.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", None, None, 0
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            text = str(obj.get("text") or obj.get("content") or "")
            tts_key = str(obj.get("tts_s3_key") or "").strip() or None
            base_key = str(obj.get("base_video_s3_key") or "").strip() or None
            ts_ms = int(obj.get("ts") or 0)
            return text, tts_key, base_key, ts_ms
    except (json.JSONDecodeError, TypeError):
        pass
    return raw, None, None, 0


class _TalkingSegment:
    """One lip-synced sentence waiting in the ready-frames queue."""

    __slots__ = ("text", "audio", "total_frames", "frames_written", "pos", "telemetry")

    def __init__(self, text: str, audio: bytes, total_frames: int, telemetry=None):
        self.text = text
        self.audio = audio
        self.total_frames = total_frames
        self.frames_written = 0
        self.pos = 0
        self.telemetry = telemetry or {}

    def next_slice(self, bytes_per_slice: int) -> bytes:
        """Next sequential audio slice (0.5s chunks), empty when exhausted."""
        end = min(self.pos + bytes_per_slice, len(self.audio))
        if self.pos >= len(self.audio):
            return b""
        seg = self.audio[self.pos : end]
        self.pos = end
        return seg


class LiveAvatarSession:
    """One avatar's continuous live session (pipe + base clip + TTS buffer)."""

    def __init__(
        self,
        avatar_id: int,
        stream_id: str,
        image_path: Path,
        base_video_path: Path,
        voice_id: str,
        work_dir: Path,
        fps: float,
        queue_key: str,
        subtitle_settings: dict | None = None,
        storage: S3Storage | None = None,
        idle_video_keys: list[str] | None = None,
        idle_video_paths: list[Path] | None = None,
        idle_switch_mode: str = "interval",
        idle_switch_seconds: int = 15,
        idle_fade_seconds: float | None = None,
        idle_motion: dict | None = None,
    ):
        self.avatar_id = avatar_id
        self.stream_id = stream_id
        self.image_path = image_path
        self.base_video_path = base_video_path
        self.voice_id = voice_id
        self.work_dir = work_dir
        self.fps = float(fps)
        self.queue_key = queue_key
        self.subtitle_settings = subtitle_settings or {}
        self.storage = storage
        self.idle_video_keys = idle_video_keys or []
        self.idle_video_paths = idle_video_paths or []
        self.idle_switch_mode = idle_switch_mode or "interval"
        self.idle_switch_seconds = max(1, int(idle_switch_seconds or 15))
        # Idle polish: crossfade between clips + procedural micro-motion
        # (breathing scale, tiny vertical drift). Per-avatar values arrive via
        # liveSettings (idleFadeSeconds / idleMotion), else env, else defaults.
        self.idle_fade_seconds = float(
            idle_fade_seconds
            if idle_fade_seconds is not None
            else _env_float("IDLE_FADE_SECONDS", 0.4)
        )
        self._idle_fade_frames = int(round(self.idle_fade_seconds * self.fps))
        idle_motion = idle_motion or {}
        self._idle_motion_enabled = bool(
            idle_motion.get("enabled", _env_bool("IDLE_MOTION_ENABLED", True))
        )
        self._breathe_amplitude = float(
            idle_motion.get(
                "breatheAmplitude", _env_float("IDLE_BREATHE_AMPLITUDE", 0.006)
            )
        )
        self._breathe_period = float(
            idle_motion.get("breathePeriod", _env_float("IDLE_BREATHE_PERIOD", 4.0))
        )
        self._drift_amplitude = float(
            idle_motion.get(
                "driftAmplitude", _env_float("IDLE_DRIFT_AMPLITUDE", 0.0015)
            )
        )
        self._idle_motion_time = 0.0
        # Crossfade state: 0 = no fade in progress. On a clip switch we keep
        # rendering the outgoing clip while blending toward the new one over
        # `_idle_fade_frames` frames (0 disables the fade = hard switch).
        self._fade_from_idx = 0
        self._fade_from_frame = 0
        self._fade_to_idx = 0
        self._fade_to_frame = 0
        self._fade_remaining = 0
        self.pipe = None
        self.base_frames: list | None = None
        # Idle clips: every pre-processed driving video pushed while the
        # avatar is not talking; the worker switches between them (interval or
        # random) so the default stream picture is not a single fixed loop.
        self.idle_clips: list[list] = []
        # S3 key of each idle clip, parallel to idle_clips (used to reuse an
        # already-loaded clip as a per-sentence action video).
        self._idle_clip_keys: list[str] = []
        # Guards idle-clip state (list, current index/position) shared between
        # the watchdog writer and the scene-switch/action producers.
        self._idle_lock = threading.Lock()
        # Per-sentence action videos (S3 key -> frames), bounded LRU cache.
        self._action_clips: dict[str, list] = {}
        self._idle_clip_idx = 0
        self._idle_frame_idx = 0
        self._idle_elapsed_frames = 0
        self._idle_switch_frames = max(1, int(self.idle_switch_seconds * self.fps))
        # Clip the current talking segment was lip-synced from (and where it
        # ended inside that clip), so idle resumes seamlessly on speech end.
        self._talk_base_clip = 0
        self._talk_base_end = 0
        self._talk_is_action = False
        self.lipsync = None
        self.tts = None
        self.ready = False

        # --- Watchdog architecture state ---
        # Ready_Frames_Queue: ("seg", _TalkingSegment) then ("frame", ndarray).
        # The writer thread consumes it at exactly fps; the inference thread
        # produces it. When empty, the writer falls back to base frames.
        self._talk_queue: queue_mod.Queue = queue_mod.Queue(maxsize=4096)
        # Finished TTS results waiting for the frame producer (text, wav|None).
        self._pending: queue_mod.Queue = queue_mod.Queue(maxsize=4)
        self._tts_results: queue_mod.Queue = queue_mod.Queue(maxsize=2)
        self._tts_thread: threading.Thread | None = None
        self._produce_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self._feed_thread: threading.Thread | None = None
        self._running = False
        self._dead = False
        self._subtitle = None
        self._subtitle_text = ""
        # Profiling/health telemetry (diagnostics only; does not affect pacing).
        self._last_health_log = 0.0
        self._last_watchdog_warn = 0.0
        self._last_write_warn = 0.0
        # Heartbeat for the recovery monitor: updated on every frame write.
        self._last_tick = 0.0
        self.payload: dict | None = None
        self._sessions_ref: dict | None = None
        self._storage = None
        self._work_root: Path | None = None

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #
    def setup(self) -> None:
        """Load the pre-processed base clip from S3 and open the ffmpeg pipe."""
        from ai.lipsync_onnx import Wav2LipOnnxLipSync
        from streaming.ffmpeg_pipe import FFmpegPipe

        if not self.base_video_path.exists():
            raise RuntimeError(f"base video not found: {self.base_video_path}")
        self.resident = ResidentRenderer(self.storage) if os.environ.get("LIPSYNC_ENGINE") == "latentsync" else None
        self.lipsync = None if self.resident else Wav2LipOnnxLipSync()
        # Optional face restoration fixes Wav2Lip lip deformation (口型变形).
        # Live follows the same FACE_ENHANCER env as offline
        # (gfpgan|codeformer|off); the auto default stays disabled because
        # full restoration is ~1s/frame and would stall the 24fps watchdog.
        # ENHANCER_MAX_FPS throttles how many frames are enhanced (e.g. 6 =
        # every ~4th frame), keeping the producer ahead of real-time.
        from ai.enhancer import create_enhancer

        self.enhancer = None if self.resident else create_enhancer(pipeline="live")
        if self.enhancer is not None:
            self.lipsync.enhancer = self.enhancer
            logger.info("live pipeline face enhancer: %s", self.enhancer.kind)
        self.base_frames, base_fps = _read_video_frames(self.base_video_path)
        # Idle clips: the base video plus any additional scene videos of the
        # configured idle scene. Clips must match the base resolution so the
        # same ffmpeg pipe can play them all; mismatched ones are skipped.
        self.idle_clips = [self.base_frames]
        self._idle_clip_keys = [self.idle_video_keys[0]] if self.idle_video_keys else [""]
        # keys[1:] align 1:1 with idle_video_paths (the extra scene videos).
        for key, p in zip(self.idle_video_keys[1:], self.idle_video_paths):
            try:
                if not p.exists():
                    logger.warning("idle video not found: %s", p)
                    continue
                frames, _ = _read_video_frames(p)
                if not frames:
                    continue
                if frames[0].shape[:2] != self.base_frames[0].shape[:2]:
                    logger.warning(
                        "skip idle video %s: shape %s != base shape %s",
                        p,
                        frames[0].shape[:2],
                        self.base_frames[0].shape[:2],
                    )
                    continue
                self.idle_clips.append(frames)
                self._idle_clip_keys.append(key)
            except Exception:
                logger.exception("failed to load idle video %s", p)
        if len(self.idle_clips) > 1:
            logger.info(
                "avatar %s idle clips: %d videos, mode=%s, switch every %ds",
                self.avatar_id,
                len(self.idle_clips),
                self.idle_switch_mode,
                self.idle_switch_seconds,
            )
        if self.resident:
            self.resident.warmup(self.idle_video_keys[0])
            logger.info("resident models ready; avatar alignment cached")
        else:
            # Preload the Wav2Lip session + face detector so the FIRST sentence
            # starts as soon as TTS lands (lazy load would add 2-4s at talk time).
            try:
                self.lipsync._load_model()
                self.lipsync._face_detect(self.base_frames[:1])
                logger.info(
                    "avatar %s lipsync model + face detector warmed up",
                    self.avatar_id,
                )
            except Exception:
                logger.exception(
                    "avatar %s lipsync warmup failed (will retry lazily)",
                    self.avatar_id,
                )

        h, w = self.base_frames[0].shape[:2]
        # Preload the subtitle renderer + CJK font BEFORE the pipe starts.
        # Loading STHeiti Medium.ttc and rendering the first overlay takes
        # ~4.8s; doing it at talk time stalls the 24fps pipe for >5s and SRS
        # kicks the publish (publish timeout 5000ms).
        try:
            if self._subtitle_enabled():
                renderer = self._make_subtitle_renderer()
                warmup = np.zeros((h, w, 3), dtype=np.uint8)
                renderer.draw(warmup, "预热字幕")
                logger.info(
                    "avatar %s subtitle renderer warmed up",
                    self.avatar_id,
                )
        except Exception:
            logger.exception(
                "avatar %s subtitle warmup failed (will retry lazily)",
                self.avatar_id,
            )

        self.pipe = FFmpegPipe(
            stream_id=self.stream_id,
            width=w,
            height=h,
            fps=self.fps,
            log_path=self.work_dir / "ffmpeg.log",
            per_frame_audio=self.resident is not None,
        )
        self.pipe.start()
        self.ready = True
        logger.info(
            "avatar %s live session ready: %d base frames @ %.1f fps, stream %s",
            self.avatar_id,
            len(self.base_frames),
            base_fps,
            self.stream_id,
        )

    # ------------------------------------------------------------------ #
    # Text intake (async TTS)
    # ------------------------------------------------------------------ #
    def maybe_start_tts(
        self,
        text: str,
        tts_s3_key: str | None = None,
        base_video_key: str | None = None,
        queued_ts: int = 0,
        narration=None,
    ) -> bool:
        """Kick off a producer-side TTS job (synthesize or S3 download)."""
        if self._tts_thread is not None and self._tts_thread.is_alive():
            return False
        self._tts_thread = threading.Thread(
            target=self._run_tts,
            args=(text, tts_s3_key, base_video_key, queued_ts, narration),
            daemon=True,
            name=f"tts-{self.stream_id}",
        )
        self._tts_thread.start()
        return True

    def _run_tts(
        self,
        text: str,
        tts_s3_key: str | None = None,
        base_video_key: str | None = None,
        queued_ts: int = 0,
        narration=None,
    ) -> None:
        """Synthesize locally or download a shared TTS wav from S3.

        Runs on the TTS thread (producer side) so the network I/O can never
        block the watchdog writer thread. The resulting wav is handed to the
        frame producer, which removes it once Wav2Lip has consumed it.
        """
        t_job = time.perf_counter()
        try:
            if self.resident:
                self._state("render", "generating")
                with self._idle_lock:
                    key = base_video_key or self._idle_clip_keys[self._idle_clip_idx % len(self._idle_clip_keys)]
                if narration:
                    for clip in self.resident.render_narration(text, key, self.work_dir,
                            narration['job_id'], narration.get('submitted_ms', 0)):
                        if not self._running:
                            break
                        self._state("render", "ready", clip_key=clip.s3_key,
                                    generation_seconds=str(round(clip.metrics['total_seconds'], 3)))
                        self._tts_results.put((text, clip, base_video_key, time.perf_counter()))
                    return
                clip = self.resident.render(text, key, self.work_dir, tts_key=tts_s3_key)
                self._state("render", "ready", clip_key=clip.s3_key,
                            generation_seconds=str(round(clip.metrics['total_seconds'],3)))
                self._tts_results.put((text, clip, base_video_key, time.perf_counter()))
                return
            out = self.work_dir / f"chunk_{int(time.time() * 1000)}.wav"
            if tts_s3_key:
                if self.storage is None:
                    raise RuntimeError(
                        "tts_s3_key provided but session has no S3 storage"
                    )
                t_dl = time.perf_counter()
                self.storage.download(tts_s3_key, out)
                logger.info(
                    "[PRODUCER] avatar %s S3 Download took %.1fms (%s)",
                    self.avatar_id,
                    (time.perf_counter() - t_dl) * 1000.0,
                    tts_s3_key,
                )
                logger.info(
                    "avatar %s TTS downloaded from S3: %s",
                    self.avatar_id,
                    tts_s3_key,
                )
            else:
                t_syn = time.perf_counter()
                self._get_tts().synthesize(text, None, out)
                logger.info(
                    "[PRODUCER] avatar %s edge-tts synthesize took %.1fms",
                    self.avatar_id,
                    (time.perf_counter() - t_syn) * 1000.0,
                )
            wav = out
            logger.info("avatar %s TTS ready (%.1fs): %s", self.avatar_id, _wav_duration(wav), text)
            logger.info(
                "[CHAIN] avatar %s TTS total %.0fms (queue_age %.0fms) audio %.1fs : %.28s",
                self.avatar_id,
                (time.perf_counter() - t_job) * 1000.0,
                time.time() * 1000.0 - queued_ts if queued_ts else 0.0,
                _wav_duration(wav),
                text,
            )
            self._tts_results.put((text, wav, base_video_key, time.perf_counter()))
        except Exception:
            self._state("render", "failed")
            logger.exception("avatar %s TTS failed for: %s", self.avatar_id, text)
            self._tts_results.put((text, None, base_video_key, time.perf_counter()))

    def _get_tts(self):
        if self.tts is None:
            if self.voice_id:
                from ai.tts_edge import EdgeTTS

                self.tts = EdgeTTS(voice_id=self.voice_id)
            else:
                from ai.tts_real import GPTSoVITSTTS

                self.tts = GPTSoVITSTTS()
        return self.tts

    def start(self, r: redis.Redis) -> None:
        """Start the watchdog writer thread and the queue-management loop."""
        if self._feed_thread is not None and self._feed_thread.is_alive():
            return
        self._redis = r
        self._running = True
        self._state("playback", "idle")
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name=f"writer-{self.stream_id}",
        )
        self._writer_thread.start()
        self._feed_thread = threading.Thread(
            target=self._run_loop,
            args=(r,),
            daemon=True,
            name=f"feed-{self.stream_id}",
        )
        self._feed_thread.start()
        threading.Thread(
            target=self._recovery_monitor,
            args=(r,),
            daemon=True,
            name=f"recover-{self.stream_id}",
        ).start()

    def _recovery_monitor(self, r: redis.Redis) -> None:
        """Rebuild the pipe if the watchdog stops making progress.

        A blocked pipe write would otherwise freeze the stream forever (SRS
        eventually kicks the publish). When the heartbeat stalls, kill ffmpeg
        (unblocks the writer via EPIPE), drop the dead session and re-run the
        session setup with the stored control payload so the stream resumes.
        """
        while self._running and not self._dead:
            if self._last_tick and time.monotonic() - self._last_tick > STALL_RECOVERY_SECONDS:
                stalled = time.monotonic() - self._last_tick
                logger.error(
                    "avatar %s watchdog stalled %.0fs without a frame — "
                    "rebuilding the live pipe",
                    self.avatar_id,
                    stalled,
                )
                self._dead = True
                self._running = False
                try:
                    self.pipe.stop(timeout=3)
                except Exception:
                    pass
                if self._sessions_ref is not None:
                    self._sessions_ref.pop(self.avatar_id, None)
                if self.payload and self._storage is not None and self._work_root is not None:
                    try:
                        _setup_session(
                            self.payload,
                            self.stream_id,
                            self._storage,
                            self._work_root,
                            self.fps,
                            self._sessions_ref or {},
                            r,
                        )
                    except Exception:
                        logger.exception(
                            "avatar %s auto-rebuild failed", self.avatar_id
                        )
                return
            time.sleep(2)

    # ------------------------------------------------------------------ #
    # Watchdog writer thread (continuous 24fps consumer)
    # ------------------------------------------------------------------ #
    def _writer_loop(self) -> None:
        """Push exactly `fps` frames/second to ffmpeg, forever.

        Ready frames (lip-synced speech) are consumed from the queue as they
        arrive; when the queue is empty the writer immediately falls back to
        the next base-animation frame + a silent audio slice. This is what
        keeps the player from buffering while Wav2Lip works.
        """
        interval = 1.0 / self.fps
        half_sec_bytes = AUDIO_SAMPLE_RATE * 2 // 2
        half_sec_frames = max(1, int(round(self.fps / 2)))
        silence_slice = b"\x00\x00" * (half_sec_bytes // 2)
        idle_count = 0
        audio_clock = 0
        cur_seg: _TalkingSegment | None = None
        seg_start_ts: float | None = None
        next_tick = time.perf_counter()
        logger.info("avatar %s watchdog writer started @ %.3fs/frame", self.avatar_id, interval)
        try:
            while self._running:
                next_tick += interval
                # After a long stall (ffmpeg backpressure), resync instead of
                # bursting missed frames into a still-congested pipe.
                if next_tick < time.perf_counter() - interval:
                    next_tick = time.perf_counter()
                t0 = time.perf_counter()
                audio_bytes = (round((audio_clock+1)*AUDIO_SAMPLE_RATE/self.fps)-round(audio_clock*AUDIO_SAMPLE_RATE/self.fps))*2
                audio_clock += 1

                # Probe: how long does the ready-frame queue block us?
                t_get = time.perf_counter()
                try:
                    kind, payload = self._talk_queue.get_nowait()
                except queue_mod.Empty:
                    kind, payload = None, None
                get_ms = (time.perf_counter() - t_get) * 1000.0

                if kind == "seg" and payload is not None:
                    cur_seg = payload
                    self._subtitle_text = payload.text
                    seg_start_ts = time.perf_counter()
                    kind = None  # this tick still writes an idle frame

                if kind == "seg_end":
                    # Producer aborted (e.g. TTS/Wav2Lip failure): abandon the
                    # current segment and fall back to idle immediately.
                    cur_seg = None
                    self._state("playback", "idle")
                    self._subtitle_text = ""
                    seg_start_ts = None
                    kind = None

                if kind == "frame" and cur_seg is not None:
                    if cur_seg.frames_written == 0 and seg_start_ts is not None:
                        logger.info(
                            "[CHAIN] avatar %s seg->first talking frame %.0fms",
                            self.avatar_id,
                            (time.perf_counter() - seg_start_ts) * 1000.0,
                        )
                        seg_start_ts = None
                        self._state("playback", "playing")
                        if cur_seg.telemetry:
                            meta = cur_seg.telemetry
                            elapsed = ((time.time()*1000-meta['submitted_ms'])/1000
                                       if meta.get('submitted_ms') else 0)
                            logger.info('[NARRATION] job=%s part=%s/%s first_frame_after=%.3fs',
                                        meta['job_id'], meta['index'], meta['count'], elapsed)
                            self._state('playback', 'playing', job_id=meta['job_id'],
                                        segment=str(meta['index']), segments=str(meta['count']))
                    if self.resident:
                        # Exact sample clock across idle/speech boundaries. Never
                        # leave half a second of idle audio ahead of a new clip.
                        self.pipe.write_audio(cur_seg.next_slice(audio_bytes).ljust(audio_bytes, b"\0"))
                    else:
                        # Pre-buffer the first 0.5s of audio before the first frame
                        # (ffmpeg waits for every input's first packet; this also
                        # gives the AAC encoder runway against writer jitter).
                        if cur_seg.frames_written == 0:
                            pre = cur_seg.next_slice(half_sec_bytes)
                            if pre:
                                self.pipe.write_audio(pre)
                        # Write the periodic audio slice BEFORE this frame: video
                        # frames are ~2.7MB at 720x1280 and the pipe write can
                        # block on ffmpeg backpressure for tens of ms. Writing
                        # audio first means encoder starvation can never be caused
                        # by a slow video write.
                        if (
                            cur_seg.frames_written > 0
                            and cur_seg.frames_written % half_sec_frames == 0
                            and cur_seg.pos < len(cur_seg.audio)
                        ):
                            audio = cur_seg.next_slice(half_sec_bytes)
                            if audio:
                                self.pipe.write_audio(audio)
                    self._write_frame(payload)
                    self._last_tick = time.monotonic()
                    cur_seg.frames_written += 1
                    if cur_seg.frames_written >= cur_seg.total_frames:
                        if not self.resident and cur_seg.pos < len(cur_seg.audio):
                            self.pipe.write_audio(cur_seg.audio[cur_seg.pos :])
                        cur_seg = None
                        self._state("playback", "idle")
                        self._subtitle_text = ""
                        # Resume idle from the clip that was actually speaking
                        # (mouth moves on the same video that was on screen),
                        # continuing where the talking loop left off. Action
                        # sentences leave the idle pool untouched.
                        if not self._talk_is_action:
                            # Cancel any in-flight idle crossfade: speech
                            # resumes at the exact clip/frame that was talking.
                            self._fade_remaining = 0
                            self._idle_clip_idx = self._talk_base_clip
                            self._idle_frame_idx = self._talk_base_end
                            self._idle_elapsed_frames = 0
                else:
                    # Idle fallback: base animation + silence.
                    idle_count += 1
                    if self.resident:
                        self.pipe.write_audio(b"\0" * audio_bytes)
                    elif idle_count % half_sec_frames == 1:
                        self.pipe.write_audio(silence_slice)
                    self._write_frame(self._idle_frame())
                    self._last_tick = time.monotonic()

                # Probe: actual work (pipe writes + subtitle overlay) and the
                # real sleep; a loop over 45ms means we miss the 24fps cadence.
                elapsed_work = time.perf_counter() - t0  # seconds, incl. queue get
                work_ms = elapsed_work * 1000.0 - get_ms
                # Pace to the deadline: sleep the bulk, busy-wait the tail.
                delay = next_tick - time.perf_counter()
                sleep_ms = 0.0
                if delay > PACING_BUSY_TAIL:
                    t_sleep = time.perf_counter()
                    time.sleep(delay - PACING_BUSY_TAIL)
                    sleep_ms = (time.perf_counter() - t_sleep) * 1000.0
                while time.perf_counter() < next_tick:
                    pass
                total_ms = (time.perf_counter() - t0) * 1000.0
                if total_ms > 45.0 and time.monotonic() - self._last_watchdog_warn >= 1.0:
                    self._last_watchdog_warn = time.monotonic()
                    logger.warning(
                        "[WATCHDOG WARNING] avatar %s loop took %.1fms (>45ms)! "
                        "queue_get=%.1fms write/work=%.1fms sleep=%.1fms",
                        self.avatar_id,
                        total_ms,
                        get_ms,
                        work_ms,
                        sleep_ms,
                    )
        except FFmpegPipeClosedError as exc:
            logger.error(
                "session %s pipe closed: %s — stopping watchdog writer; "
                "restart the live to rebuild the pipe",
                self.stream_id,
                exc,
            )
            self._dead = True
            self._running = False
            if self.pipe is not None:
                self.pipe.stop()
        except Exception:
            logger.exception("session %s writer error, stopping", self.stream_id)
            self._dead = True
            self._running = False
            if self.pipe is not None:
                self.pipe.stop()

    def _idle_frame(self):
        with self._idle_lock:
            clips = self.idle_clips or [self.base_frames]
            if self._fade_remaining > 0:
                frame = self._idle_fade_frame(clips)
            else:
                clip = clips[self._idle_clip_idx % len(clips)]
                frame = clip[self._idle_frame_idx % len(clip)]
                self._idle_frame_idx += 1
                self._idle_elapsed_frames += 1
                if (
                    len(clips) > 1
                    and self._idle_elapsed_frames >= self._idle_switch_frames
                ):
                    self._switch_idle_clip()
        return self._apply_idle_motion(frame)

    def _idle_fade_frame(self, clips):
        """One blended frame between the outgoing and incoming idle clips.
        Caller must hold `self._idle_lock`."""
        import numpy as np

        from_clip = clips[self._fade_from_idx % len(clips)]
        to_clip = clips[self._fade_to_idx % len(clips)]
        f0 = from_clip[self._fade_from_frame % len(from_clip)]
        f1 = to_clip[self._fade_to_frame % len(to_clip)]
        total = max(1, self._idle_fade_frames)
        alpha = 1.0 - self._fade_remaining / total
        frame = (
            f0.astype(np.float32) * (1.0 - alpha)
            + f1.astype(np.float32) * alpha
        ).astype(np.uint8)
        self._fade_from_frame += 1
        self._fade_to_frame += 1
        self._fade_remaining -= 1
        # Keep the mirrored index on the outgoing clip so a sentence that
        # starts mid-fade lip-syncs from the clip actually on screen.
        self._idle_frame_idx = self._fade_from_frame
        self._idle_elapsed_frames += 1
        if self._fade_remaining <= 0:
            self._idle_clip_idx = self._fade_to_idx
            self._idle_frame_idx = self._fade_to_frame
            self._fade_remaining = 0
        return frame

    def _apply_idle_motion(self, frame):
        """Procedural micro-motion on an idle frame: a slow breathing scale
        plus a tiny vertical drift, both sine-modulated on a continuous
        per-session clock so the motion survives clip switches. Pure CPU
        (numpy + OpenCV warp) and well under the 24fps frame budget."""
        self._idle_motion_time += 1.0 / self.fps
        if not self._idle_motion_enabled:
            return frame
        if self._breathe_amplitude <= 0.0 and self._drift_amplitude <= 0.0:
            return frame
        import cv2
        import numpy as np

        h, w = frame.shape[:2]
        t = self._idle_motion_time
        period = max(0.5, self._breathe_period)
        scale = 1.0 + self._breathe_amplitude * math.sin(2.0 * math.pi * t / period)
        dy = self._drift_amplitude * h * math.sin(
            2.0 * math.pi * t / (period * 0.7) + 1.3
        )
        cx, cy = w / 2.0, h / 2.0
        m = np.array(
            [[scale, 0.0, cx * (1.0 - scale)], [0.0, scale, cy * (1.0 - scale) + dy]],
            dtype=np.float32,
        )
        return cv2.warpAffine(
            frame, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )

    def _switch_idle_clip(self) -> None:
        """Switch to the next (interval) or a random (random) idle clip and
        schedule the next switch: fixed N seconds, or a random 5-30s window.
        The visual switch is a short crossfade when enabled, never a hard cut.
        Caller must hold `self._idle_lock`."""
        n = len(self.idle_clips)
        if n <= 1:
            self._idle_elapsed_frames = 0
            return
        if self.idle_switch_mode == "random":
            candidates = [i for i in range(n) if i != self._idle_clip_idx] or [0]
            to_idx = random.choice(candidates)
            lo = max(5, self.idle_switch_seconds // 2)
            hi = max(30, self.idle_switch_seconds * 2)
            self._idle_switch_frames = max(
                1, random.randint(int(lo * self.fps), int(hi * self.fps))
            )
            logger.info(
                "avatar %s idle -> random clip %d/%d (next switch in %.1fs)",
                self.avatar_id,
                to_idx + 1,
                n,
                self._idle_switch_frames / self.fps,
            )
        else:
            to_idx = (self._idle_clip_idx + 1) % n
            self._idle_switch_frames = max(
                1, int(self.idle_switch_seconds * self.fps)
            )
            logger.info(
                "avatar %s idle -> clip %d/%d (switch every %ds)",
                self.avatar_id,
                to_idx + 1,
                n,
                self.idle_switch_seconds,
            )
        self._idle_elapsed_frames = 0
        if self._idle_fade_frames <= 0:
            # Fade disabled: hard switch, same as the pre-crossfade behaviour.
            self._idle_clip_idx = to_idx
            self._idle_frame_idx = 0
            self._fade_remaining = 0
            return
        self._fade_from_idx = self._idle_clip_idx
        self._fade_from_frame = self._idle_frame_idx
        self._fade_to_idx = to_idx
        self._fade_to_frame = 0
        self._fade_remaining = self._idle_fade_frames
        logger.info(
            "avatar %s idle crossfade clip %d -> %d (%.2fs)",
            self.avatar_id,
            self._fade_from_idx + 1,
            to_idx + 1,
            self._fade_remaining / self.fps,
        )

    # ------------------------------------------------------------------ #
    # Queue management loop (lightweight; no pipe I/O)
    # ------------------------------------------------------------------ #
    def tick(self, r: redis.Redis) -> None:
        """Pop text, hand finished TTS to the producer, keep queues fed."""
        # 0) Every 5s: queue health + process CPU (diagnostics only).
        now = time.monotonic()
        if now - self._last_health_log >= 5.0:
            self._last_health_log = now
            cpu: float | None = None
            try:
                import psutil

                cpu = psutil.Process().cpu_percent(interval=None)
            except Exception:
                pass
            logger.info(
                "[HEALTH] avatar %s ready_frames=%d pending=%d tts_results=%d "
                "tts_alive=%s cpu=%s%%",
                self.avatar_id,
                self._talk_queue.qsize(),
                self._pending.qsize(),
                self._tts_results.qsize(),
                self._tts_thread is not None and self._tts_thread.is_alive(),
                "%.1f" % cpu if cpu is not None else "n/a",
            )

        # 1) Pull new text when no TTS is in flight and pending has room.
        if (
            self._tts_thread is None or not self._tts_thread.is_alive()
        ) and not self._pending.full():
            raw = r.lpop(self.queue_key)
            if raw:
                text, tts_s3_key, base_video_key, queued_ts = parse_live_message(raw)
                narration = None
                try:
                    entry = json.loads(raw)
                    if isinstance(entry, dict) and entry.get('narration') is True:
                        narration = {'job_id': entry.get('job_id') or f'legacy-{time.time_ns()}',
                                     'submitted_ms': entry.get('submitted_ms', 0)}
                except (ValueError, TypeError):
                    pass
                if text or tts_s3_key:
                    if queued_ts and time.time()*1000-queued_ts > 120000:
                        logger.info("dropping expired live reply")
                        return
                    if queued_ts:
                        logger.info(
                            "[CHAIN] avatar %s queue age %.0fms (backend push -> worker pop)",
                            self.avatar_id,
                            time.time() * 1000.0 - queued_ts,
                        )
                    self.maybe_start_tts(text, tts_s3_key, base_video_key, queued_ts, narration)

        # 2) Move finished TTS results into the producer's pending queue.
        try:
            text, wav, base_video_key, tts_ready_ts = self._tts_results.get_nowait()
            self._pending.put((text, wav, base_video_key, tts_ready_ts))
        except queue_mod.Empty:
            pass

        if self.resident:
            if not hasattr(self, '_host_director'):
                self._host_director = HostDirector(self.avatar_id)
            playback = r.hgetall(f'live_playback:{self.avatar_id}')
            idle = (not (self._tts_thread and self._tts_thread.is_alive())
                    and not (self._produce_thread and self._produce_thread.is_alive())
                    and self._pending.empty() and self._tts_results.empty()
                    and self._talk_queue.empty()
                    and playback.get('playback') != 'playing')
            manual_busy = bool(r.llen(self.queue_key) or
                              (self._tts_thread and self._tts_thread.is_alive()) or
                              not self._pending.empty() or not self._tts_results.empty())
            with self._idle_lock:
                base_key = self._idle_clip_keys[self._idle_clip_idx % len(self._idle_clip_keys)]
            item = self._host_director.tick(r, self.resident, base_key, self.work_dir, idle, manual_busy)
            if item:
                text, clip = item
                self._pending.put((text, clip, base_key, time.perf_counter()))

        # 3) Start the frame producer when it is idle and work is pending.
        if (
            self._produce_thread is None or not self._produce_thread.is_alive()
        ) and not self._pending.empty():
            text, wav, base_video_key, tts_ready_ts = self._pending.get_nowait()
            self._produce_thread = threading.Thread(
                target=self._produce_segment,
                args=(text, wav, base_video_key, tts_ready_ts),
                daemon=True,
                name=f"produce-{self.stream_id}",
            )
            self._produce_thread.start()
        time.sleep(0.05)

    def _run_loop(self, r: redis.Redis) -> None:
        while self._running:
            if self._dead:
                break
            try:
                self.tick(r)
            except redis.exceptions.ConnectionError:
                # Redis was restarted (e.g. the RedisStack switch): back off
                # quietly instead of spamming tracebacks every 0.2s. The pool
                # reconnects on the next successful tick.
                logger.warning(
                    "session %s redis connection lost, retrying in 1s",
                    self.stream_id,
                )
                time.sleep(1.0)
            except FFmpegPipeClosedError as exc:
                logger.error(
                    "session %s pipe closed: %s — stopping feed loop; "
                    "restart the live to rebuild the pipe",
                    self.stream_id,
                    exc,
                )
                self._dead = True
                self._running = False
                self.close()
                break
            except Exception:
                logger.exception("session %s feed error, continuing", self.stream_id)
                time.sleep(0.2)

    # ------------------------------------------------------------------ #
    # Inference producer (async; Wav2Lip in small batches)
    # ------------------------------------------------------------------ #
    def _produce_segment(
        self,
        text: str,
        wav,
        base_video_key: str | None = None,
        tts_ready_ts: float | None = None,
    ) -> None:
        """Lip-sync `wav` in small batches and push frames to the writer queue.

        Runs on its own thread so it never blocks the 24fps writer. The segment
        marker is pushed BEFORE the frames so the writer can start the talking
        state as soon as the first batch lands; time-to-first-frame is one
        Wav2Lip batch (~8 frames).
        """
        if wav is None:
            logger.warning("avatar %s chunk skipped (TTS failed)", self.avatar_id)
            return
        if isinstance(wav, CompletedClip):
            self._produce_completed_clip(text, wav)
            return
        t_prod = time.perf_counter()
        if tts_ready_ts:
            logger.info(
                "[CHAIN] avatar %s TTS ready -> producer started %.0fms",
                self.avatar_id,
                (t_prod - tts_ready_ts) * 1000.0,
            )
        try:
            # Probe: NumPy/SciPy audio + mel pre-processing (before ONNX).
            t_pre = time.perf_counter()
            audio = self.lipsync.audio_pcm16(wav)
            n_frames = len(self.lipsync._mel_chunks(wav, self.fps))
            seg = _TalkingSegment(text, audio, n_frames)
            self._talk_queue.put(("seg", seg))
            if base_video_key:
                base_slice = self._action_slice(base_video_key, n_frames)
            else:
                base_slice, _ = self._slice_current_idle(n_frames)
            pre_ms = (time.perf_counter() - t_pre) * 1000.0
            logger.info(
                "[PRODUCER] avatar %s preprocessing took %.1fms "
                "(audio+mel+base, %d frames)",
                self.avatar_id,
                pre_ms,
                n_frames,
            )

            # Probe: face detection + OpenCV pre-processing + ONNX inference,
            # broken down via the iter_frames timing hook.
            stage_ms: dict[str, float] = {}

            def _timing(stage: str, ms: float) -> None:
                stage_ms[stage] = stage_ms.get(stage, 0.0) + ms

            t_inf = time.perf_counter()
            frames = self.lipsync.iter_frames(
                wav, base_slice, self.fps, timing=_timing
            )
            pushed = 0
            t_first = None
            for frame in frames:
                if t_first is None:
                    t_first = time.perf_counter()
                self._talk_queue.put(("frame", frame))
                pushed += 1
            inf_ms = (time.perf_counter() - t_inf) * 1000.0
            logger.info(
                "[PRODUCER] avatar %s Wav2Lip Inference took %.1fms "
                "for %d frames (face_detect=%.1fms opencv_preprocess=%.1fms "
                "onnx=%.1fms)",
                self.avatar_id,
                inf_ms,
                pushed,
                stage_ms.get("face_detect", 0.0),
                stage_ms.get("preprocess", 0.0),
                stage_ms.get("onnx_batch", 0.0),
            )
            logger.info(
                "[CHAIN] avatar %s produce: first-frame %.0fms total %.0fms (%d frames)",
                self.avatar_id,
                (t_first - t_prod) * 1000.0 if t_first else 0.0,
                (time.perf_counter() - t_prod) * 1000.0,
                pushed,
            )
            logger.info(
                "avatar %s talking segment queued: %d frames (%.1fs speech)",
                self.avatar_id,
                pushed,
                len(audio) / 2 / AUDIO_SAMPLE_RATE,
            )
        except Exception:
            logger.exception("avatar %s segment production failed", self.avatar_id)
            # Let the watchdog drop the unfinished segment and go back to idle.
            try:
                self._talk_queue.put(("seg_end", None))
            except Exception:
                pass
        finally:
            # Zero-leak: the TTS wav (S3-downloaded or locally synthesized)
            # is removed as soon as Wav2Lip has consumed it.
            if isinstance(wav, Path):
                try:
                    wav.unlink(missing_ok=True)
                except OSError:
                    logger.warning("failed to remove temp tts file %s", wav)

    def _state(self, field, value, **extra):
        try:
            r = getattr(self, "_redis", None)
            if r is not None:
                mapping = {field: value, field + "_at": str(time.time()), **extra}
                r.hset(f"live_playback:{self.avatar_id}", mapping=mapping)
                r.expire(f"live_playback:{self.avatar_id}", 86400)
        except Exception:
            logger.warning("failed to report playback state")

    def _produce_completed_clip(self, text, clip):
        """Predecode the complete generated clip before publishing a segment.

        No partial video or asynchronous frame starvation can interrupt speech.
        """
        import cv2
        normalized = clip.path.with_name(clip.path.stem + "-play.mp4")
        try:
            meta = clip.metrics.get('narration', {})
            trim = (["-ss", "0.4", "-t", str(clip.metrics['latent']['audio_seconds'])]
                    if meta.get('trim_padding') else [])
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip.path), *trim,
                "-an", "-vf", f"fps={self.fps}", "-c:v", "libx264", "-preset", "fast",
                "-crf", "18", str(normalized)], check=True)
            frames, _ = _read_video_frames(normalized)
            if not frames or frames[0].shape != self.base_frames[0].shape:
                raise RuntimeError("Generated clip dimensions do not match idle video")
            audio = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(clip.path), *trim,
                "-vn", "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1"])
            target = round(len(frames) * AUDIO_SAMPLE_RATE / self.fps) * 2
            audio = audio[:target].ljust(target, b"\0")
            self._talk_is_action = True
            self._talk_queue.put(("seg", _TalkingSegment(text, audio, len(frames), meta)))
            for frame in frames:
                while self._running:
                    try:
                        self._talk_queue.put(("frame", frame), timeout=1)
                        break
                    except queue_mod.Full:
                        continue
                if not self._running:
                    break
            logger.info("resident complete clip queued: %d frames key=%s", len(frames), clip.s3_key)
        except Exception:
            self._state("render", "failed")
            logger.exception("resident clip preparation failed")
            self._talk_queue.put(("seg_end", None))
        finally:
            normalized.unlink(missing_ok=True)
            clip.path.unlink(missing_ok=True)

    def _write_frame(self, frame) -> None:
        """Apply the subtitle overlay then push the frame into the pipe."""
        if self._subtitle_text and self._subtitle_enabled():
            if self._subtitle is None:
                from streaming.subtitle import SubtitleRenderer

                self._subtitle = self._make_subtitle_renderer()
            frame = self._subtitle.draw(frame, self._subtitle_text)
        t_write = time.perf_counter()
        self.pipe.write_frame(frame)
        write_ms = (time.perf_counter() - t_write) * 1000.0
        if write_ms > 20.0 and time.monotonic() - self._last_write_warn >= 5.0:
            self._last_write_warn = time.monotonic()
            logger.warning(
                "[WATCHDOG] avatar %s pipe.write_frame took %.1fms "
                "(ffmpeg backpressure?)",
                self.avatar_id,
                write_ms,
            )

    def _subtitle_enabled(self) -> bool:
        return bool(self.subtitle_settings.get("subtitleEnabled", True))

    def _make_subtitle_renderer(self):
        from streaming.subtitle import SubtitleRenderer

        font_name = (self.subtitle_settings.get("subtitleFont") or "").strip()
        font_path = SubtitleRenderer.resolve_font_path(font_name)
        return SubtitleRenderer(
            font_path=font_path,
            font_size=int(self.subtitle_settings.get("subtitleSize") or 46),
            position=self.subtitle_settings.get("subtitlePosition") or "bottom",
            border_width=int(self.subtitle_settings.get("subtitleBorder") or 2),
        )

    def _slice_current_idle(self, n: int) -> tuple[list, int]:
        """Slice the clip that is currently on screen (the idle clip the
        writer is pushing right now) as the Wav2Lip base, so the mouth moves
        on the same video the viewer sees — not always the default clip."""
        with self._idle_lock:
            clips = self.idle_clips or [self.base_frames]
            idx = self._idle_clip_idx % len(clips)
            clip = clips[idx]
            start = self._idle_frame_idx % len(clip)
            seg = [clip[(start + k) % len(clip)] for k in range(n)]
            self._talk_base_clip = idx
            self._talk_base_end = (start + n) % len(clip)
        self._talk_is_action = False
        return seg, idx

    def _action_slice(self, base_key: str, n: int) -> list:
        """Slice a specific action video (agentic <action:key> selection) as
        the Wav2Lip base for ONE sentence. The idle pool is left untouched —
        when the sentence ends the writer resumes the clip that was showing."""
        clip = self._load_action_frames(base_key)
        if clip is None:
            logger.warning(
                "avatar %s action video unavailable (%s); falling back to idle clip",
                self.avatar_id,
                base_key,
            )
            seg, _ = self._slice_current_idle(n)
            return seg
        seg = [clip[k % len(clip)] for k in range(n)]
        self._talk_is_action = True
        return seg

    def _load_action_frames(self, base_key: str) -> list | None:
        """Return pre-loaded frames for an action video, reusing an already
        loaded idle clip or a cached action clip; otherwise download + read
        (bounded LRU cache, resolution must match the base clip)."""
        # Reuse an idle clip that is already in memory (same S3 key).
        with self._idle_lock:
            for key, clip in zip(self._idle_clip_keys, self.idle_clips):
                if key == base_key:
                    return clip
        cached = self._action_clips.get(base_key)
        if cached is not None:
            return cached
        if self.storage is None:
            return None
        try:
            path = self.work_dir / f"action_{abs(hash(base_key))}.mp4"
            self.storage.download(base_key, path)
            frames, _ = _read_video_frames(path)
            if not frames:
                return None
            with self._idle_lock:
                base_shape = (self.base_frames or self.idle_clips[0])[0].shape[:2]
            if frames[0].shape[:2] != base_shape:
                logger.warning(
                    "skip action video %s: shape %s != base shape %s",
                    base_key,
                    frames[0].shape[:2],
                    base_shape,
                )
                return None
            # Bound the cache (keep the 4 most recent action videos).
            self._action_clips[base_key] = frames
            if len(self._action_clips) > 4:
                self._action_clips.pop(next(iter(self._action_clips)))
            logger.info(
                "avatar %s action video loaded: %s (%d frames)",
                self.avatar_id,
                base_key,
                len(frames),
            )
            return frames
        except Exception:
            logger.exception("failed to load action video %s", base_key)
            return None

    def switch_idle_pool(self, keys: list[str]) -> None:
        """Swap the Watchdog's idle video pool (scene switch). Downloads the
        new videos first, then atomically replaces the in-memory clip list
        under `_idle_lock` so the writer never sees a half-updated pool."""
        if not keys:
            logger.warning("avatar %s switch_scene got an empty video pool", self.avatar_id)
            return
        if self.storage is None:
            logger.error("avatar %s cannot switch scene: no S3 storage", self.avatar_id)
            return
        new_clips: list[list] = []
        new_keys: list[str] = []
        for i, key in enumerate(keys):
            try:
                path = self.work_dir / f"idle_switch_{i}.mp4"
                self.storage.download(key, path)
                frames, _ = _read_video_frames(path)
                if not frames:
                    continue
                with self._idle_lock:
                    base_shape = (self.base_frames or self.idle_clips[0])[0].shape[:2]
                if frames[0].shape[:2] != base_shape:
                    logger.warning(
                        "skip switch video %s: shape %s != base shape %s",
                        key,
                        frames[0].shape[:2],
                        base_shape,
                    )
                    continue
                new_clips.append(frames)
                new_keys.append(key)
            except Exception:
                logger.exception("failed to download scene-switch video %s", key)
        if not new_clips:
            logger.error("avatar %s switch_scene produced no usable videos", self.avatar_id)
            return
        with self._idle_lock:
            self.idle_clips = new_clips
            self._idle_clip_keys = new_keys
            self._idle_clip_idx = 0
            self._idle_frame_idx = 0
            self._idle_elapsed_frames = 0
            self._fade_remaining = 0
            self._idle_switch_frames = max(1, int(self.idle_switch_seconds * self.fps))
        logger.info(
            "avatar %s idle pool switched to %d video(s): %s",
            self.avatar_id,
            len(new_clips),
            new_keys,
        )

    def close(self) -> None:
        self._running = False
        if hasattr(self, '_host_director'):
            self._host_director.close()
        for thread in (self._writer_thread, self._feed_thread, self._produce_thread):
            if (
                thread is not None
                and thread.is_alive()
                and thread is not threading.current_thread()
            ):
                thread.join(timeout=5)
        if self.pipe is not None:
            self.pipe.stop()


def _wav_duration(wav: Path) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
        capture_output=True,
        text=True,
    )
    try:
        return float(probe.stdout.strip())
    except ValueError:
        return 0.0


def _control_listener(
    r: redis.Redis,
    control_key: str,
    sessions: dict,
    storage: S3Storage,
    work_root: Path,
    fps: float,
) -> None:
    """Daemon thread: handle start/stop control messages for avatar sessions."""
    while True:
        try:
            # Drop sessions whose ffmpeg pipe died so a new start can rebuild.
            for aid, sess in list(sessions.items()):
                if getattr(sess, "_dead", False):
                    logger.warning(
                        "avatar %s session dead, removing it so a new start can rebuild",
                        aid,
                    )
                    sessions.pop(aid, None)

            item = r.blpop(control_key, timeout=1)
            if item is None:
                continue
            payload = json.loads(item[1])
            action = payload.get("action")
            avatar_id = int(payload.get("avatarId"))
            stream_id = payload.get("streamId") or f"avatar_{avatar_id}"

            # Agentic scene switch: swap a running session's idle video pool.
            if payload.get("type") == "control" and action == "switch_scene":
                session = sessions.get(avatar_id)
                if session is None:
                    logger.info(
                        "avatar %s switch_scene ignored (session not running)",
                        avatar_id,
                    )
                    continue
                video_pool = payload.get("video_pool") or []
                threading.Thread(
                    target=session.switch_idle_pool,
                    args=(video_pool,),
                    daemon=True,
                    name=f"switch-{stream_id}",
                ).start()
                continue

            if action == "stop":
                session = sessions.pop(avatar_id, None)
                if session:
                    session.close()
                    logger.info("avatar %s live session stopped", avatar_id)
                continue

            if avatar_id in sessions:
                logger.info("avatar %s live session already running", avatar_id)
                continue

            threading.Thread(
                target=_setup_session,
                args=(payload, stream_id, storage, work_root, fps, sessions, r),
                daemon=True,
                name=f"setup-{stream_id}",
            ).start()
        except redis.RedisError:
            logger.exception("redis error in control listener, retrying in 5s")
            time.sleep(5)
        except Exception:
            logger.exception("unexpected control listener error, continuing")
            time.sleep(1)


def _setup_session(payload, stream_id, storage, work_root, fps, sessions, r) -> None:
    try:
        avatar_id = int(payload["avatarId"])
        work_dir = work_root / f"live-{stream_id}"
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        image_path = work_dir / ("image" + Path(payload["imageS3Key"]).suffix)
        storage.download(payload["imageS3Key"], image_path)
        idle_keys = payload.get("idleVideos") or []
        if not idle_keys:
            idle_keys = [payload.get("baseVideoS3Key", "")]
        if not idle_keys[0]:
            logger.error(
                "control message for %s has no idle/base video; "
                "run the avatar pre-processing step first",
                stream_id,
            )
            return
        live_settings = payload.get("liveSettings") or {}
        base_video_path = work_dir / "base_video.mp4"
        storage.download(idle_keys[0], base_video_path)
        idle_paths = []
        for i, key in enumerate(idle_keys[1:], start=1):
            p = work_dir / f"idle_{i}.mp4"
            try:
                storage.download(key, p)
                idle_paths.append(p)
            except Exception:
                logger.exception("failed to download idle video %s", key)
        session = LiveAvatarSession(
            avatar_id=avatar_id,
            stream_id=stream_id,
            image_path=image_path,
            base_video_path=base_video_path,
            voice_id=payload.get("voiceId", ""),
            work_dir=work_dir,
            fps=fps,
            queue_key=f"live_queue:{avatar_id}",
            subtitle_settings=live_settings,
            storage=storage,
            idle_video_paths=idle_paths,
            idle_switch_mode=payload.get("idleSwitchMode") or "interval",
            idle_switch_seconds=int(payload.get("idleSwitchSeconds") or 15),
            idle_video_keys=idle_keys,
            idle_fade_seconds=live_settings.get("idleFadeSeconds"),
            idle_motion=live_settings.get("idleMotion") or {},
        )
        session.setup()
        # Stash what is needed to auto-rebuild the session if the pipe stalls.
        session.payload = payload
        session._sessions_ref = sessions
        session._storage = storage
        session._work_root = work_root
        sessions[avatar_id] = session
        session.start(r)
    except SystemExit:
        # Interpreter shutdown interrupted a mid-flight setup thread; this is
        # benign (the process is exiting anyway) — keep the log clean.
        pass
    except Exception:
        logger.exception("failed to set up live session for %s", payload.get("streamId"))


def _restore_sessions(api_base_url, sessions, storage, work_root, fps, r) -> None:
    """Re-start live sessions that are persisted in the DB after a worker
    restart (the DB row survives but the in-memory pipe does not)."""
    url = f"{api_base_url}/api/live"
    for attempt in range(5):
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            items = resp.json().get("data", [])
            for item in items:
                aid = int(item["avatarId"])
                if aid in sessions:
                    continue
                stream_id = item.get("streamId") or f"avatar_{aid}"
                payload = {
                    "action": "start",
                    "avatarId": aid,
                    "streamId": stream_id,
                    "imageS3Key": item.get("imageS3Key", ""),
                    "baseVideoS3Key": item.get("baseVideoS3Key", ""),
                    "idleVideos": item.get("idleVideos") or [],
                    "idleSwitchMode": item.get("idleSwitchMode") or "interval",
                    "idleSwitchSeconds": item.get("idleSwitchSeconds") or 15,
                    "voiceId": item.get("voiceId", ""),
                    "liveSettings": item.get("liveSettings") or {},
                }
                if not payload["imageS3Key"] or not payload["idleVideos"]:
                    logger.warning(
                        "session for avatar %s missing image/idle video keys, skip restore",
                        aid,
                    )
                    continue
                logger.info("restoring live session for avatar %s", aid)
                threading.Thread(
                    target=_setup_session,
                    args=(payload, stream_id, storage, work_root, fps, sessions, r),
                    daemon=True,
                    name=f"restore-{aid}",
                ).start()
            return
        except Exception as exc:
            logger.warning(
                "live session restore attempt %d/%d failed: %s",
                attempt + 1,
                5,
                exc,
            )
            time.sleep(5)


def main() -> None:
    _load_local_env()
    _check_required_env()
    # Reduce GIL handoff latency so the watchdog's time.sleep() reacquires the
    # GIL quickly and the 24fps deadline is met (default 5ms switch interval
    # alone drops the effective frame rate below 24fps).
    sys.setswitchinterval(0.001)
    # Diagnostics: STREAM_FAULTHANDLER=1 dumps all thread stacks to stderr
    # every 15s (used to catch silent hangs/deaths).
    if os.environ.get("STREAM_FAULTHANDLER"):
        import faulthandler

        faulthandler.dump_traceback_later(15, repeat=True)
    if os.environ.get("TTS_ENGINE", "edge") == "gpt-sovits":
        _ensure_nltk_resources()
    cfg = load_config()
    work_root = cfg["work_root"]
    fps = float(os.environ.get("LIVEPORTRAIT_OUTPUT_FPS", "24"))
    control_key = os.environ.get(
        "LIVE_CONTROL_QUEUE_KEY", "talking_avatar:live_control"
    )

    storage = S3Storage()
    r = redis.Redis(
        host=cfg["redis_host"],
        port=cfg["redis_port"],
        password=cfg["redis_password"] or None,
        db=cfg["redis_db"],
        decode_responses=True,
    )

    sessions: dict[int, LiveAvatarSession] = {}
    from streaming.relay import RelayManager
    relay_manager = RelayManager(r, sessions)
    relay_thread = threading.Thread(target=relay_manager.run, daemon=True, name="external-relay")
    relay_thread.start()
    threading.Thread(
        target=_control_listener,
        args=(r, control_key, sessions, storage, work_root, fps),
        daemon=True,
        name="live-control",
    ).start()
    threading.Thread(
        target=_restore_sessions,
        args=(cfg["api_base_url"], sessions, storage, work_root, fps, r),
        daemon=True,
        name="live-restore",
    ).start()

    def _shutdown(*_args):
        relay_manager.close()
        relay_thread.join(timeout=5)
        logger.info("shutting down %d live session(s)", len(sessions))
        for session in sessions.values():
            session.close()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("stream worker started: control=%s fps=%s", control_key, fps)

    # Feed loops now run per session (see LiveAvatarSession.start); the main
    # thread only keeps the process alive for signal handling.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
