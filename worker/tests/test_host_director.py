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

    def test_async_prefetch_keeps_its_own_text_and_index(self):
        pending = []
        def submit(fn):
            future = Future()
            pending.append((fn, future))
            return future
        self.director.executor.submit = submit
        self.tick()
        fn, future = pending.pop(0)
        future.set_result(fn())
        first = self.tick()  # schedules paragraph 2, then pops paragraph 1
        self.assertEqual(first[0], self.config['scripts'][0])
        fn, future = pending.pop(0)
        future.set_result(fn())  # execution happens after the pop reassigned locals
        second = self.tick()
        self.assertEqual(second[0], self.config['scripts'][1])
        self.assertEqual(second[1].metrics['narration']['job_id'], 'host-test-1')
        self.assertEqual(self.renderer.render.call_args.args[0], self.config['scripts'][1])

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

    def test_library_prepares_all_then_replays_without_model_calls(self):
        self.config.update(kind='library', welcome_count=2,
                           scripts=['欢迎来坐一会儿。', '欢迎来到这里。', '我是数字人知夏。', '今天放松一下吧。'])
        self.save()
        for _ in range(4):
            self.assertIsNone(self.tick())
        self.assertEqual(self.renderer.render.call_count, 4)
        seen = []
        for _ in range(12):
            item = self.tick()
            self.assertIsNotNone(item)
            seen.append(item[0])
            item[1].path.unlink()  # stream consumer owns and removes only playback copy
        self.assertEqual(self.renderer.render.call_count, 4)
        for offset in [0, 4, 8]:
            self.assertEqual(set(seen[offset:offset+4]), set(self.config['scripts']))
            self.assertIn(seen[offset], self.config['scripts'][:2])
            self.assertIn(seen[offset+1], self.config['scripts'][2:])
        self.assertEqual(len(self.director.library), 4)
        self.assertEqual(json.loads(self.objects['live_host_status:1'])['phase'], 'speaking')
        self.assertTrue(all(c.path.exists() for _, c in self.director.library.values()))
        self.assertIsNone(self.tick(manual=True))
        self.config.update(mode='paused', command='pause'); self.save()
        self.assertIsNone(self.tick())
        self.config.update(mode='running', command='resume'); self.save()
        self.assertIsNotNone(self.tick())
        self.config.update(mode='stopped', command='stop'); self.save()
        self.assertIsNone(self.tick())
        self.assertFalse(self.director.library)

    def test_preparation_is_serialized_between_avatars(self):
        from threading import Event, Lock
        from concurrent.futures import ThreadPoolExecutor
        entered = Event()
        release = Event()
        counter_lock = Lock()
        calls = []
        def render(text, base, directory):
            with counter_lock:
                calls.append(text)
                first = len(calls) == 1
            if first:
                entered.set()
                release.wait(3)
            path = Path(directory)/(text+'.mp4')
            path.write_bytes(b'video')
            return CompletedClip(path, 'cache', {})
        self.renderer.render.side_effect = render
        other_config = dict(self.config, run_id='other')
        self.objects['live_host:2'] = json.dumps(other_config)
        self.director.executor = ThreadPoolExecutor(max_workers=1)
        other = HostDirector(2)
        self.addCleanup(other.close)
        self.tick()
        self.assertTrue(entered.wait(2))
        other.tick(self.redis, self.renderer, 'base', self.temp.name, True, False)
        self.assertEqual(len(calls), 1)
        release.set()
        self.director.future[2].result(timeout=3)
        other.future[2].result(timeout=3)
        self.assertEqual(len(calls), 2)

    def test_library_base_change_requires_new_run(self):
        self.config.update(kind='library', welcome_count=1); self.save()
        self.tick()
        self.director.tick(self.redis, self.renderer, 'different-base', self.temp.name, True, False)
        self.assertIn('底片', self.director.error)
        self.assertEqual(json.loads(self.objects['live_host_status:1'])['phase'], 'failed')



class WardrobeHintTests(unittest.TestCase):
    setUp = HostDirectorTests.setUp
    save = HostDirectorTests.save
    def test_hints_are_rate_limited_and_never_selected_as_normal_paragraphs(self):
        self.config.update(kind='library',welcome_count=1,wardrobe_enabled=True,wardrobe_hint_indices=[2],
                           scripts=['欢迎朋友。','聊聊今天。','想看换装可以发换衣服。'])
        self.save()
        def tick(available=True,manual=False):
            return self.director.tick(self.redis,self.renderer,'base',self.temp.name,True,manual,wardrobe_available=available)
        for _ in range(3):self.assertIsNone(tick())
        # Let the time gate pass; three ordinary paragraphs are still required.
        self.director.next_wardrobe_hint=0
        first=[tick()[0] for _ in range(3)]
        self.assertNotIn(self.config['scripts'][2],first)
        self.assertIsNone(tick(manual=True))
        hint=tick();self.assertEqual(hint[0],self.config['scripts'][2])
        later=[tick()[0] for _ in range(6)]
        self.assertNotIn(self.config['scripts'][2],later)
        self.director.next_wardrobe_hint=0
        unavailable=[tick(available=False)[0] for _ in range(4)]
        self.assertNotIn(self.config['scripts'][2],unavailable)
        self.assertEqual(tick()[0],self.config['scripts'][2])
        self.config.update(mode='paused',command='pause');self.save()
        self.assertIsNone(tick())

if __name__=='__main__': unittest.main()
