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
from ai.audio_windows import AudioWindows

class AudioWindowTests(unittest.TestCase):
    def test_eof_has_right_context_without_repeating_or_padding_spoken_audio(self):
        for length in (1, 959, 960, 961, 24000, 24001, 48633):
            with self.subTest(length=length):
                pcm = np.arange(length, dtype=np.float32)
                assembler = AudioWindows(24000)
                windows = assembler.feed(pcm, final=True)
                np.testing.assert_array_equal(np.concatenate([w[0] for w in windows]), pcm)
                for core, window, left in windows:
                    needed = left + (len(core)+959)//960
                    self.assertGreaterEqual(len(window), (needed+4)*960)

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

    def test_prepared_look_adds_profile_without_replacing_model(self):
        model = self.service.model
        from unittest.mock import Mock
        model.register_prepared = Mock()
        look={'profile':'ruotang_blue','base_key':'scene_videos/blue.mp4','input_sha256':'a'*64}
        with patch.object(self.service,'AVATARS',dict(self.service.AVATARS)),patch('ai.prepared_looks.prepared_looks',return_value={'3001':look}):
            renderer=MuseRenderer(3001)
            renderer.warmup(look['base_key'])
            renderer.warmup(look['base_key'])
            model.register_prepared.assert_called_once_with('ruotang_blue','a'*64)
            self.assertIs(self.service.model,model)
            events=list(renderer.events('换好衣服啦。',look['base_key']))
            self.assertEqual(events[-1]['type'],'done')

class AppearanceRoutingTests(unittest.TestCase):
    def test_new_appearance_routes_separately_and_original_stays_on_existing_service(self):
        with patch.dict(os.environ, {'MUSETALK_RESIDENT_URL':'http://original', 'MUSETALK_WARDROBE_URL':'http://wardrobe', 'RESIDENT_API_TOKEN':'test'}):
            self.assertEqual(MuseRenderer(3).url, 'http://original')
            self.assertEqual(MuseRenderer(3002).url, 'http://wardrobe')
            self.assertEqual(MuseRenderer(3003).url, 'http://wardrobe')
            self.assertEqual(MuseRenderer(2).url, 'http://original')

if __name__ == '__main__':unittest.main()
