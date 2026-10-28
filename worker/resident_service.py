"""Run in the EXISTING cloud model environment: one process/model/GPU.

Only JSON and short-lived S3 URLs cross HTTP. Model repositories stay unchanged.
Bind to loopback and expose through SSH forwarding; tokens stay in private env.
"""
import argparse
from collections import OrderedDict
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def transfer(url, path, upload=False):
    parsed = urlsplit(url)
    if parsed.scheme != 'http' or parsed.netloc != os.environ['RESIDENT_S3_HOST']:
        raise ValueError('Only the configured private S3 endpoint is allowed')
    if upload:
        request = Request(url, data=path.read_bytes(), method='PUT')
        with urlopen(request, timeout=60) as response:
            response.read()
    else:
        with urlopen(url, timeout=60) as response, path.open('wb') as out:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise ValueError('Input exceeds 100MB')
                out.write(chunk)


def duration(path):
    return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                         'format=duration', '-of', 'csv=p=0', str(path)]))


def model_cache_identity(root, mode):
    """Metadata fingerprint: replace models/configs then restart the resident service."""
    rows = []
    for folder in ('checkpoints', 'configs', 'sd-vae-ft-mse', 'indextts', 'latentsync'):
        for path in sorted((root/folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                stat = path.stat()
                rows.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    voice = root/'examples/voice_09.wav'
    data = dict(mode=mode, files=rows,
                voice=hashlib.sha256(voice.read_bytes()).hexdigest() if voice.exists() else None,
                service=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                steps=int(os.environ.get('LATENTSYNC_STEPS', '20')) if mode == 'latentsync' else None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class IndexTTSResident:
    def __init__(self, root):
        sys.path.insert(0, str(root))
        os.chdir(root)
        from indextts.infer_v2_5 import IndexTTS2
        self.model = IndexTTS2(cfg_path=str(root/'checkpoints/config.yaml'),
                               model_dir=str(root/'checkpoints'), use_bf16=True)
        self.speaker = str(root/'examples/voice_09.wav')
        self.cache_identity = model_cache_identity(root, 'indextts')

    def run(self, body, work):
        text = body.get('text', '').strip()
        if not text or len(text) > 240:
            raise ValueError('Expected 1–240 characters')
        out = work/'speech.wav'
        start = time.perf_counter()
        self.model.infer(spk_audio_prompt=self.speaker, text=text, lang='ZH',
                         output_path=str(out), duration_factor=1.0, verbose=False)
        return out, {'inference_seconds': time.perf_counter()-start, 'audio_seconds': duration(out)}


class LatentSyncResident:
    def __init__(self, root):
        os.chdir(root)
        sys.path.insert(0, str(root))
        import torch
        from omegaconf import OmegaConf
        from diffusers import AutoencoderKL, DDIMScheduler
        from latentsync.models.unet import UNet3DConditionModel
        import latentsync.pipelines.lipsync_pipeline as module
        from latentsync.whisper.audio2feature import Audio2Feature
        self.torch = torch
        self.config = OmegaConf.load(root/'configs/unet/second_stage.yaml')
        self.dtype = torch.float16
        audio = Audio2Feature(model_path=str(root/'checkpoints/whisper/tiny.pt'),
                              device='cuda', num_frames=self.config.data.num_frames)
        vae = AutoencoderKL.from_pretrained(str(root/'sd-vae-ft-mse'), torch_dtype=self.dtype)
        vae.config.scaling_factor, vae.config.shift_factor = 0.18215, 0
        unet, _ = UNet3DConditionModel.from_pretrained(OmegaConf.to_container(self.config.model),
                     str(root/'checkpoints/latentsync_unet.pt'), device='cpu')
        self.pipeline = module.LipsyncPipeline(vae=vae, audio_encoder=audio,
             unet=unet.to(dtype=self.dtype),
             scheduler=DDIMScheduler.from_pretrained(str(root/'configs'))).to('cuda')
        # The upstream creates a face-landmark model per call. Cache that object
        # inside this dedicated single-job process, without editing upstream code.
        original_processor = module.ImageProcessor
        processors = {}
        def processor(resolution, mask='fix_mask', device='cuda'):
            key = (resolution, mask, device)
            if key not in processors:
                processors[key] = original_processor(resolution, mask=mask, device=device)
            return processors[key]
        module.ImageProcessor = processor
        self.processor = processor
        self.aligned = OrderedDict()
        self.target_frames = 0
        self.hit = False
        original_align = self.pipeline.affine_transform_video
        self.original_align = original_align
        def aligned_video(path):
            key = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            self.hit = key in self.aligned
            if key not in self.aligned:
                self.aligned[key] = original_align(path)
                while len(self.aligned) > 2:
                    self.aligned.popitem(last=False)
            self.aligned.move_to_end(key)
            faces, frames, boxes, matrices = self.aligned[key]
            n = self.target_frames or len(faces)
            indices = [i % len(faces) for i in range(n)]
            return (faces[indices], frames[indices], [boxes[i] for i in indices],
                    [matrices[i] for i in indices])
        self.pipeline.affine_transform_video = aligned_video
        self.cache_identity = model_cache_identity(root, 'latentsync')

    def run(self, body, work):
        base = work/'base.mp4'
        steps = body.get('steps', int(os.environ.get('LATENTSYNC_STEPS', '20')))
        if type(steps) is not int or steps < 8 or steps > 50:
            raise ValueError('steps must be an integer between 8 and 50')
        transfer(body['video_url'], base)
        normalized = work/'base-25.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(base), '-an',
                        '-vf', 'fps=25', '-c:v', 'libx264', '-crf', '18', str(normalized)], check=True)
        if body.get('warmup'):
            self.pipeline.image_processor = self.processor(self.config.data.resolution)
            self.target_frames = 0
            self.pipeline.affine_transform_video(str(normalized))
            return None, {'alignment_cached': True}
        audio = work/'speech.wav'
        transfer(body['audio_url'], audio)
        speech_duration = duration(audio)
        if speech_duration > 20:
            raise ValueError('Speech exceeds live clip duration limit')
        # Cover the complete spoken sentence, even though upstream emits only
        # complete batches of 16 frames. Padding is silence, never cut speech.
        frames = max(16, math.ceil((speech_duration+.8)*25/16)*16)
        self.target_frames = frames
        padded = work/'speech-input.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(audio),
                        '-af', 'adelay=400,apad', '-t', str(frames/25+.15),
                        '-ar', '16000', '-ac', '1', str(padded)], check=True)
        out = work/'generated.mp4'
        from accelerate.utils import set_seed
        set_seed(1247)
        started = time.perf_counter()
        old = Path.cwd()
        # Upstream's temp directory is relative: isolate it per request.
        os.chdir(work)
        try:
            self.pipeline(video_path=str(normalized), audio_path=str(padded),
                video_out_path=str(out), num_frames=self.config.data.num_frames,
                num_inference_steps=steps, guidance_scale=1.5, weight_dtype=self.dtype,
                width=self.config.data.resolution, height=self.config.data.resolution)
        finally:
            os.chdir(old)
        if not out.exists() or duration(out) < speech_duration+.4:
            raise RuntimeError('Generated clip does not cover the full sentence')
        return out, {'inference_seconds': time.perf_counter()-started,
                      'audio_seconds': speech_duration, 'video_seconds': duration(out),
                      'alignment_cache_hit': self.hit, 'steps': steps}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['tts', 'latent'])
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--work', type=Path, required=True)
    args = p.parse_args()
    token = os.environ['RESIDENT_API_TOKEN']
    args.work.mkdir(parents=True, exist_ok=True)
    model = None
    error = None
    mutex = threading.Lock()
    loaded_at = None
    def load():
        nonlocal model, error, loaded_at
        try:
            started = time.perf_counter()
            model = (IndexTTSResident if args.mode == 'tts' else LatentSyncResident)(args.root)
            loaded_at = time.time()
            print(json.dumps({'event':'model_loaded', 'mode':args.mode,
                              'seconds':time.perf_counter()-started}), flush=True)
        except Exception as exc:
            error = type(exc).__name__
            import traceback
            traceback.print_exc()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # Never log signed URLs or Authorization headers.
        def send(self, code, data):
            raw = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            if self.path != '/readyz':
                return self.send(404, {'error':'not found'})
            self.send(200 if model else 503, {'ready':bool(model), 'mode':args.mode,
                       'pid':os.getpid(), 'loaded_at':loaded_at, 'error':error,
                       'cache_identity':getattr(model, 'cache_identity', None)})
        def do_POST(self):
            if self.path != '/v1/jobs':
                return self.send(404, {'error':'not found'})
            if not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
                return self.send(401, {'error':'unauthorized'})
            if not model:
                return self.send(503, {'error':'model loading'})
            if not mutex.acquire(blocking=False):
                return self.send(503, {'error':'busy'})
            started = time.perf_counter()
            try:
                length = int(self.headers.get('Content-Length', 0))
                if length <= 0 or length > 32768:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length))
                with tempfile.TemporaryDirectory(dir=args.work) as directory:
                    with open('/tmp/digital-human-latentsync.lock', 'a+b') as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        output, metrics = model.run(body, Path(directory))
                    if output:
                        transfer(body['upload_url'], output, upload=True)
                    metrics.update(total_seconds=time.perf_counter()-started, pid=os.getpid())
                    self.send(200, {'s3_key':body.get('output_key'), 'metrics':metrics})
            except BlockingIOError:
                self.send(503, {'error':'GPU busy'})
            except ValueError as exc:
                self.send(400, {'error':str(exc)})
            except Exception:
                import traceback
                traceback.print_exc()
                self.send(500, {'error':'inference failed; see private service log'})
            finally:
                mutex.release()
    threading.Thread(target=load, daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
