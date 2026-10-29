"""CosyVoice -> MuseTalk frame stream; private host API, bounded media cache."""
import base64
import gzip
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import requests
import soundfile as sf
from scipy.signal import resample_poly
from ai.audio_windows import AudioWindows

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('MUSETALK_ROOT', '/data1/taoxin/lingcast-musetalk-bench'))
AVATARS = json.loads((HERE.parent/'deploy/musetalk-avatars.json').read_text())
TOKEN = os.environ['RESIDENT_API_TOKEN']
COSY = os.environ.get('COSYVOICE_URL', 'http://127.0.0.1:59495')
CACHE = ROOT/'production-cache'
CACHE.mkdir(exist_ok=True)
busy = threading.Lock()
model = None
failure = None
fingerprint = None

def load():
    global model, failure, fingerprint
    try:
        from ai.musetalk_runtime import StreamingMuse
        instance = StreamingMuse()
        # Fingerprint model code, reference conditions and checkpoint metadata.
        paths = [Path(__file__), HERE/'ai/musetalk_runtime.py', HERE/'ai/audio_windows.py',
                 HERE/'cosyvoice_service.py', Path('/data1/taoxin/lingcast-cosyvoice-bench/prompt.wav'),
                 Path('/data1/taoxin/lingcast-cosyvoice-bench/prompt.txt')]
        weights = list((ROOT/'source/models').rglob('*.pth')) + list((Path('/data1/taoxin/lingcast-cosyvoice-bench/model')).rglob('*.pt'))
        identity = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        identity += [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(weights)]
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        model = instance
    except Exception as error:
        failure = type(error).__name__
        logging.exception('MuseTalk startup failed')

def windows(text, stop):
    """Keep TTS generating the next audio window while MuseTalk renders this one."""
    output = queue.Queue(maxsize=2)
    def put(value):
        while not stop.is_set():
            try:
                output.put(value, timeout=.5)
                return
            except queue.Full:
                pass
    def produce():
        try:
            assembler = None
            finished = False
            with requests.post(COSY+'/speech', json={'text': text}, stream=True, timeout=(5, 60)) as response:
                response.raise_for_status()
                for line in response.iter_lines(chunk_size=None):
                    if stop.is_set():
                        return
                    if not line:
                        continue
                    part = json.loads(line)
                    if 'error' in part:
                        raise RuntimeError('CosyVoice '+part['error'])
                    finished = part.get('done', False)
                    if finished:
                        pcm = np.empty(0, dtype=np.float32)
                    else:
                        assembler = assembler or AudioWindows(int(part['sample_rate']))
                        pcm = np.frombuffer(base64.b64decode(part['pcm']), dtype='<f4').copy()
                    if assembler:
                        for core, window, left in assembler.feed(pcm, final=finished):
                            put((core, window, left, assembler.rate))
                    if finished:
                        break
            if not finished:
                raise RuntimeError('Incomplete CosyVoice stream')
            put(None)
        except Exception as error:
            put(error)
    threading.Thread(target=produce, daemon=True).start()
    while not stop.is_set():
        value = output.get(timeout=90)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return
        yield value

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/readyz':
            return self.json({'ready': model is not None, 'error': failure, 'avatars': AVATARS, 'engine': 'cosyvoice-musetalk'})
        self.json({'error': 'not found'}, 404)

    def do_POST(self):
        if self.path != '/render':
            return self.json({'error': 'not found'}, 404)
        if self.headers.get('Origin') or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+TOKEN):
            return self.json({'error': 'unauthorized'}, 401)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('invalid size')
            data = json.loads(self.rfile.read(length))
            avatar = AVATARS[str(data['avatar_id'])]
            text = data['text'].strip()
            if not 1 <= len(text) <= 240 or data['base_key'] != avatar['base_key']:
                raise ValueError('unsupported text or avatar material')
        except (ValueError, KeyError, TypeError, AttributeError):
            return self.json({'error': 'Unsupported avatar/material or invalid text'}, 400)
        if model is None:
            return self.json({'error': 'not ready'}, 503)
        digest = hashlib.sha256(json.dumps([fingerprint, avatar, text], sort_keys=True).encode()).hexdigest()
        cached = CACHE/(digest+'.ndjson.gz')
        hit = cached.exists()
        if not hit and not busy.acquire(False):
            return self.json({'error': 'busy'}, 503)
        pending = CACHE/(digest+'.'+uuid.uuid4().hex+'.tmp')
        stop = threading.Event()
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header('X-Media-Cache', 'hit' if hit else 'miss')
        self.end_headers()
        def send(line):
            self.wfile.write(f'{len(line):x}\r\n'.encode()+line+b'\r\n')
            self.wfile.flush()
        try:
            if hit:
                os.utime(cached, None)
                with gzip.open(cached, 'rb') as stream:
                    for line in stream:
                        send(line)
            else:
                with gzip.open(pending, 'wb', compresslevel=1) as cache, tempfile.TemporaryDirectory(dir=ROOT) as work:
                    def event(value):
                        line = (json.dumps(value)+'\n').encode()
                        cache.write(line)
                        send(line)
                    offset = 0
                    for index, (core, window, left, rate) in enumerate(windows(text, stop)):
                        count = math.ceil(len(core)/rate*25)
                        path = Path(work)/'window.wav'
                        sf.write(path, window, rate, subtype='PCM_16')
                        # Resample each core to the exact 16k/25fps playback clock.
                        divisor = math.gcd(rate, 16000)
                        pcm = resample_poly(core, 16000//divisor, rate//divisor)
                        pcm = np.pad(pcm, (0, max(0, count*640-len(pcm))))[:count*640]
                        pcm = (np.clip(pcm, -1, 1)*32767).astype('<i2').tobytes()
                        event({'type': 'audio', 'index': index, 'frames': count, 'pcm': base64.b64encode(pcm).decode()})
                        emitted = 0
                        def frame(image, number):
                            nonlocal emitted
                            if left <= number < left+count:
                                ok, jpeg = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                                if not ok:
                                    raise RuntimeError('JPEG encoding failed')
                                event({'type': 'frame', 'index': index, 'jpeg': base64.b64encode(jpeg).decode()})
                                emitted += 1
                        model.render(avatar['profile'], path, frame, frame_offset=offset-left)
                        if emitted != count:
                            raise RuntimeError('Incomplete MuseTalk window')
                        offset += count
                    event({'type': 'done'})
                pending.replace(cached)
                for old in sorted(CACHE.glob('*.ndjson.gz'), key=lambda p: p.stat().st_mtime, reverse=True)[256:]:
                    old.unlink(missing_ok=True)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            logging.exception('Streaming render failed')
            try:
                send((json.dumps({'type': 'error', 'message': type(error).__name__})+'\n').encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            stop.set()
            pending.unlink(missing_ok=True)
            try:
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            if not hit:
                busy.release()

if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 59496), Handler)
    threading.Thread(target=load, daemon=True).start()
    server.serve_forever()
