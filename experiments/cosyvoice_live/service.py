"""Isolated CosyVoice3 PCM stream. No production queues or public listener."""
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '2'
os.environ['OMP_NUM_THREADS'] = '4'
import base64
import json
import logging
from pathlib import Path
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path('/data1/taoxin/lingcast-cosyvoice-bench')
sys.path[:0] = [str(ROOT/'source-pinned'), str(ROOT/'source-pinned/third_party/Matcha-TTS')]
os.chdir(ROOT/'source-pinned')
ready = False
failure = None
model = None
busy = threading.Lock()

def synthesize(text):
    return model.inference_zero_shot(text, '', '', zero_shot_spk_id='lingcast',
                                     stream=True, text_frontend=False)

def load():
    global ready, failure, model
    try:
        import torch
        torch.set_num_threads(4)
        torch.cuda.set_per_process_memory_fraction(.14)
        from cosyvoice.cli.cosyvoice import AutoModel
        model = AutoModel(model_dir=str(ROOT/'model'), fp16=True)
        # Upstream grows this field across calls, even after warmup. Keep the
        # trained 25-token chunk size stable for interactive first-packet latency.
        model.model.token_hop_len = 25
        model.model.stream_scale_factor = 1
        prompt = (ROOT/'prompt.txt').read_text().strip()
        model.add_zero_shot_spk('You are a helpful assistant.<|endofprompt|>'+prompt,
                               str(ROOT/'prompt.wav'), 'lingcast')
        for _ in synthesize('欢迎来到直播间，很高兴见到你。'):
            pass
        ready = True
    except Exception as error:
        logging.exception('CosyVoice startup failed')
        failure = type(error).__name__

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args):
        pass

    def json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/readyz':
            return self.json(dict(ready=ready, error=failure, gpu=2, model='CosyVoice3-0.5B-2512'))
        self.json({'error': 'not found'}, 404)

    def do_POST(self):
        if self.path != '/speech':
            return self.json({'error': 'not found'}, 404)
        if self.headers.get('Origin'):
            return self.json({'error': 'server clients only'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError('invalid size')
            text = json.loads(self.rfile.read(size))['text']
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 240:
                raise ValueError('expected 1-240 characters')
        except (ValueError, KeyError, TypeError):
            return self.json({'error': 'invalid request'}, 400)
        if not ready:
            return self.json({'error': 'not ready'}, 503)
        if not busy.acquire(False):
            return self.json({'error': 'busy'}, 409)
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        start = time.perf_counter()
        def send(data):
            payload = (json.dumps(data)+'\n').encode()
            self.wfile.write(f'{len(payload):x}\r\n'.encode()+payload+b'\r\n')
            self.wfile.flush()
        try:
            for part in synthesize(text.strip()):
                pcm = part['tts_speech'].float().cpu().numpy().reshape(-1).astype('<f4')
                send(dict(pcm=base64.b64encode(pcm.tobytes()).decode(),
                          sample_rate=model.sample_rate, elapsed_ms=(time.perf_counter()-start)*1000))
            send(dict(done=True, elapsed_ms=(time.perf_counter()-start)*1000))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            logging.exception('CosyVoice inference failed')
            send({'error': type(error).__name__})
        finally:
            try:
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            busy.release()

if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 59495), Handler)
    threading.Thread(target=load, daemon=True).start()
    server.serve_forever()
