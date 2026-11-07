import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import numpy as np
import requests
from ai.musetalk_client import MuseRenderer


class CosyVoiceAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'source-pinned').mkdir()
        cwd = os.getcwd()
        paths = list(__import__('sys').path)
        try:
            with patch.dict(os.environ, {'COSYVOICE_ROOT': str(root), 'COSYVOICE_API_TOKEN': 'private-test-token', 'COSYVOICE_GPU': '0', 'COSYVOICE_MEMORY_FRACTION': '0.65'}):
                spec = importlib.util.spec_from_file_location('cosyvoice_auth_test_service', Path(__file__).resolve().parents[1] / 'cosyvoice_service.py')
                self.service = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self.service)
        finally:
            os.chdir(cwd)
            __import__('sys').path[:] = paths
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.service.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}/speech'

    def test_speech_rejects_missing_or_wrong_token_before_model_use(self):
        for headers in ({}, {'Authorization': 'Bearer wrong'}):
            with self.subTest(headers=headers):
                response = requests.post(self.url, json={'text': 'hello'}, headers=headers, timeout=5)
                self.assertEqual(response.status_code, 401)

    def test_valid_token_reaches_service_readiness_check(self):
        response = requests.post(self.url, json={'text': 'hello'}, headers={'Authorization': 'Bearer private-test-token'}, timeout=5)
        self.assertEqual(response.status_code, 503)

    def test_empty_token_preserves_local_compatibility(self):
        with patch.object(self.service, 'API_TOKEN', ''):
            response = requests.post(self.url, json={'text': 'hello'}, timeout=5)
        self.assertEqual(response.status_code, 503)

    def test_readiness_reports_the_configured_gpu(self):
        response = requests.get(self.url.replace('/speech', '/readyz'), timeout=5)
        self.assertEqual(response.json()['gpu'], 0)
        self.assertEqual(self.service.MEMORY_FRACTION, 0.65)


class CosyVoiceClientAuthTests(unittest.TestCase):
    def test_renderer_sends_its_configured_speech_token(self):
        class SpeechHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.headers.get('Authorization') != 'Bearer remote-test-token':
                    self.send_response(401)
                    self.end_headers()
                    return
                payload = b'\n'.join([
                    json.dumps({'sample_rate': 24000, 'pcm': base64.b64encode(np.zeros(960, dtype='<f4').tobytes()).decode()}).encode(),
                    b'{"done":true}',
                ]) + b'\n'
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), SpeechHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'MUSETALK_ROOT': root, 'RESIDENT_API_TOKEN': 'renderer-test'}):
            import musetalk_service as service
        url = f'http://127.0.0.1:{server.server_port}'
        with patch.object(service, 'COSY', url), patch.object(service, 'COSY_TOKEN', 'remote-test-token'):
            windows = list(service.windows('hello', threading.Event()))
        self.assertEqual(len(windows), 1)
        self.assertEqual(len(windows[0][0]), 960)

    def test_renderer_defaults_to_local_endpoint_and_prefers_its_own_token(self):
        with patch.dict(os.environ, {'MUSETALK_API_TOKEN': 'muse-token', 'RESIDENT_API_TOKEN': 'legacy-token'}, clear=True):
            renderer = MuseRenderer(2)
        self.assertEqual(renderer.url, 'http://127.0.0.1:59496')
        self.assertEqual(renderer.token, 'muse-token')

    def test_renderer_uses_its_token_for_remote_readiness_check(self):
        with patch.dict(os.environ, {'MUSETALK_RESIDENT_URL': 'https://example.invalid', 'MUSETALK_API_TOKEN': 'muse-token'}, clear=True):
            renderer = MuseRenderer(2)
            with patch('ai.musetalk_client.requests.get') as get:
                get.return_value.json.return_value = {'ready': True, 'avatars': {'2': {'base_key': 'asset'}}}
                renderer.warmup('asset')
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer muse-token')

    def test_local_tunnel_can_be_identified_as_remote_speech(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'MUSETALK_ROOT': root, 'RESIDENT_API_TOKEN': 'renderer-test'}):
            from musetalk_service import speech_is_remote
        self.assertTrue(speech_is_remote('http://127.0.0.1:59495', True))
        self.assertFalse(speech_is_remote('http://127.0.0.1:59495', False))
        self.assertTrue(speech_is_remote('https://example.invalid', False))

    def test_renderer_service_accepts_its_token_without_legacy_token(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'MUSETALK_ROOT': root, 'MUSETALK_API_TOKEN': 'muse-token'}, clear=True):
            spec = importlib.util.spec_from_file_location('muse_auth_test_service', Path(__file__).resolve().parents[1] / 'musetalk_service.py')
            service = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(service)
        self.assertEqual(service.TOKEN, 'muse-token')
        self.assertTrue(service.CACHE_ENABLED)


if __name__ == '__main__':
    unittest.main()
