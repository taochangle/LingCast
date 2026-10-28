"""Admin-controlled external RTMP relay. Publishing credentials never enter logs."""
import json
import os
import subprocess
import threading
import time


class RelayManager:
    def __init__(self, redis_client, sessions):
        self.redis = redis_client
        self.sessions = sessions
        self.stop_event = threading.Event()
        self.processes = {}
        self.revisions = {}
        self.failed = set()

    def status(self, avatar_id, state):
        self.redis.set(f"live_relay_status:{avatar_id}", json.dumps({
            "state": state, "updatedAt": time.time(),
        }), ex=10)

    def terminate(self, avatar_id):
        process = self.processes.pop(avatar_id, None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception:
                    # Do not log payloads, process arguments, or credentials.
                    pass
                self.stop_event.wait(1)
        finally:
            for avatar_id in list(self.processes):
                self.terminate(avatar_id)

    def tick(self):
        configs = {}
        for key in self.redis.scan_iter("live_relay_config:*"):
            avatar_id = int(key.rsplit(":", 1)[1])
            configs[avatar_id] = json.loads(self.redis.get(key) or "{}")
        for avatar_id in set(self.processes) - configs.keys():
            self.terminate(avatar_id)
        for avatar_id, config in configs.items():
            revision = config.get("revision")
            if self.revisions.get(avatar_id) != revision:
                self.terminate(avatar_id)
                self.failed.discard(avatar_id)
                self.revisions[avatar_id] = revision
            if not config.get("enabled"):
                self.terminate(avatar_id)
                self.status(avatar_id, "stopped")
                continue
            session = self.sessions.get(avatar_id)
            if session is None or not session.ready or not session._running:
                self.terminate(avatar_id)
                self.status(avatar_id, "waiting")
                continue
            process = self.processes.get(avatar_id)
            if process and process.poll() is not None:
                self.terminate(avatar_id)
                self.failed.add(avatar_id)
            if avatar_id in self.failed:
                self.status(avatar_id, "error")
                continue
            if avatar_id not in self.processes:
                target = config["server"].rstrip("/") + "/" + config["streamKey"]
                cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "quiet",
                       "-rw_timeout", "15000000", "-i", os.environ.get("RELAY_SOURCE_RTMP_URL", "rtmp://127.0.0.1:1935/live/avatar_{avatar_id}").format(avatar_id=avatar_id),
                       "-map", "0:v:0", "-map", "0:a:0", "-vf",
                       "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
                       "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                       "-profile:v", "main", "-pix_fmt", "yuv420p", "-b:v", "3000k",
                       "-minrate", "3000k", "-maxrate", "3000k", "-bufsize", "6000k",
                       "-x264-params", "nal-hrd=cbr:force-cfr=1", "-g", "30", "-keyint_min", "30",
                       "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                       "-ac", "2", "-rw_timeout", "15000000", "-f", "flv",
                       "-flvflags", "no_duration_filesize", target]
                self.processes[avatar_id] = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status(avatar_id, "running")

    def close(self):
        self.stop_event.set()
