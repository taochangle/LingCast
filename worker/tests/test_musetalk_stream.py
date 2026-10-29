import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import numpy as np
import requests
from http.server import ThreadingHTTPServer
from ai.musetalk_client import MuseRenderer

class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {'MUSETALK_ROOT': self.temp.name, 'RESIDENT_API_TOKEN': 'test'}):
            import musetalk_service as service
        self.service = service
        self.calls = 0
        class Model:
            def render(_, name, path, callback, frame_offset):
                self.calls += 1
                callback(np.zeros((8, 8, 3), dtype=np.uint8), 0)
        self.patches = [patch.object(service, 'ROOT', Path(self.temp.name)),
            patch.object(service, 'CACHE', Path(self.temp.name)/'cache'),
            patch.object(service, 'TOKEN', 'test'), patch.object(service, 'fingerprint', 'version1'),
            patch.object(service, 'model', Model()),
            patch.object(service, 'windows', return_value=iter([(np.ones(960, dtype=np.float32)*.1, np.zeros(960), 0, 24000)]))]
        for p in self.patches:
            p.start();self.addCleanup(p.stop)
        service.CACHE.mkdir()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), service.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)
        self.env = patch.dict(os.environ, {'MUSETALK_RESIDENT_URL': self.url, 'RESIDENT_API_TOKEN': 'test'})
        self.env.start();self.addCleanup(self.env.stop)
        self.base = service.AVATARS['2']['base_key']

    def test_stream_clock_and_cache_skip_both_models(self):
        renderer = MuseRenderer(2)
        renderer.warmup(self.base)
        first = list(renderer.events('欢迎你。', self.base))
        self.assertEqual([p['type'] for p in first], ['audio', 'frame', 'done'])
        self.assertEqual(len(first[0]['audio']), 1280)
        self.assertFalse(first[0]['cache_hit'])
        second = list(renderer.events('欢迎你。', self.base))
        self.assertTrue(second[0]['cache_hit']);self.assertEqual(self.calls, 1)
        self.assertEqual(first[0]['audio'], second[0]['audio'])
        # Cached speech must remain available while a different GPU job runs.
        self.service.busy.acquire()
        try:
            cached = list(renderer.events('欢迎你。', self.base))
            self.assertTrue(cached[0]['cache_hit'])
        finally:
            self.service.busy.release()

    def test_reject_wrong_material_and_untrusted_browser(self):
        renderer = MuseRenderer(2)
        with self.assertRaises(RuntimeError):renderer.warmup('wrong-face')
        r = requests.post(self.url+'/render', json={'avatar_id':2,'text':'hi','base_key':'wrong'},headers={'Authorization':'Bearer test'})
        self.assertEqual(r.status_code, 400)
        r = requests.post(self.url+'/render', json={},headers={'Origin':'http://example.com','Authorization':'Bearer test'})
        self.assertEqual(r.status_code, 401);self.assertEqual(self.calls, 0)

    def test_incomplete_stream_is_never_reported_as_complete(self):
        class Response:
            status_code=200
            headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def raise_for_status(self):pass
            def iter_lines(self,**kwargs):
                yield json.dumps({'type':'audio','index':0,'frames':1,'pcm':base64.b64encode(b'\0'*1280).decode()}).encode()
        with patch('ai.musetalk_client.requests.post',return_value=Response()):
            with self.assertRaises(RuntimeError):list(MuseRenderer(2).events('hi',self.base))

if __name__ == '__main__':unittest.main()
