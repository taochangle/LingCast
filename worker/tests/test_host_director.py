from concurrent.futures import Future
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from ai.host_director import HostDirector
from ai.resident_client import CompletedClip


class ImmediateExecutor:
    def __init__(self, **kwargs): pass
    def submit(self, fn):
        future = Future()
        try: future.set_result(fn())
        except Exception as error: future.set_exception(error)
        return future
    def shutdown(self, **kwargs): pass


class HostDirectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.objects = {}
        self.redis = Mock()
        self.redis.get.side_effect = self.objects.get
        self.redis.set.side_effect = self.objects.__setitem__
        self.config = dict(run_id='host-test', mode='running', command='1', scripts=['第一段主持内容。','第二段主持内容。','第三段主持内容。'], min_pause=0, max_pause=0)
        self.save()
        self.renderer = Mock()
        def render(text, base, directory):
            path = Path(directory)/f'{self.renderer.render.call_count}.mp4'
            path.write_bytes(b'video')
            return CompletedClip(path, 'cache', {'latent': {'audio_seconds': 3}})
        self.renderer.render.side_effect = render
        with patch('ai.host_director.ThreadPoolExecutor', ImmediateExecutor):
            self.director = HostDirector(1)
        self.addCleanup(self.director.close)

    def save(self): self.objects['live_host:1'] = json.dumps(self.config)
    def tick(self, idle=True, manual=False):
        return self.director.tick(self.redis, self.renderer, 'base', self.temp.name, idle, manual)

    def test_manual_priority_resume_and_finish(self):
        self.assertIsNone(self.tick(manual=True))
        self.renderer.render.assert_not_called()
        self.assertIsNone(self.tick()) # prefetch
        self.assertIsNone(self.tick(manual=True)) # prepared, but manual gets priority
        first = self.tick()
        self.assertEqual(first[0], self.config['scripts'][0])
        self.assertIsNone(self.tick(idle=False, manual=True))
        self.assertEqual(self.director.completed, 0)
        second = self.tick()
        self.assertEqual(second[0], self.config['scripts'][1])
        self.tick(idle=False)
        third = self.tick()
        self.assertEqual(third[0], self.config['scripts'][2])
        self.tick()
        state=json.loads(self.objects['live_host_status:1'])
        self.assertEqual((state['phase'],state['completed']),('finished',3))
        self.assertEqual(self.renderer.render.call_count,3)

    def test_pause_stop_and_restart_resume(self):
        self.tick()
        self.config.update(mode='paused',command='2'); self.save()
        self.assertIsNone(self.tick())
        self.assertEqual(len(self.director.ready),1)
        self.config.update(mode='running',command='3'); self.save()
        self.assertIsNotNone(self.tick())
        self.config.update(mode='paused',command='4'); self.save()
        self.tick() # allow current paragraph to finish
        self.assertEqual(self.director.completed,1)
        self.director.close()
        with patch('ai.host_director.ThreadPoolExecutor', ImmediateExecutor):
            self.director=HostDirector(1)
        self.assertIsNone(self.tick())
        self.assertEqual(self.director.completed,1)
        self.config.update(mode='running',command='5'); self.save()
        self.tick()
        second=self.tick()
        self.assertEqual(second[0],self.config['scripts'][1])
        self.config.update(mode='stopped',command='6'); self.save()
        self.assertIsNone(self.tick())
        self.assertFalse(self.director.ready)

    def test_prepare_failure_requires_explicit_retry(self):
        render=self.renderer.render.side_effect
        self.renderer.render.side_effect=RuntimeError('failed')
        self.tick(); self.tick()
        self.assertEqual(json.loads(self.objects['live_host_status:1'])['phase'],'failed')
        self.tick()
        self.assertEqual(self.renderer.render.call_count,1)
        self.renderer.render.side_effect=render
        self.config.update(command='retry');self.save()
        self.tick()
        self.assertIsNotNone(self.tick())


if __name__=='__main__': unittest.main()
