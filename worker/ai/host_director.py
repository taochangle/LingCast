"""Bounded, lower-priority autonomous presenter; Redis owns the session plan."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import random
import time

logger = logging.getLogger(__name__)


class HostDirector:
    def __init__(self, avatar_id):
        self.key = f'live_host:{avatar_id}'
        self.status_key = f'live_host_status:{avatar_id}'
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f'host-{avatar_id}')
        self.config = {}
        self.ready = deque()
        self.future = None
        self.completed = 0
        self.active = False
        self.error = ''
        self.due = 0
        self.closed = False
        self.last_status = None

    @staticmethod
    def discard(clip):
        clip.path.unlink(missing_ok=True)

    def report(self, r, phase):
        status = dict(run_id=self.config.get('run_id'), phase=phase, completed=self.completed,
                      total=len(self.config.get('scripts', [])), prepared=len(self.ready),
                      error=self.error)
        if status != self.last_status:
            r.set(self.status_key, json.dumps(status, ensure_ascii=False))
            self.last_status = status

    def tick(self, r, renderer, base_key, work_dir, pipeline_idle, manual_busy):
        if self.closed:
            return None
        raw = r.get(self.key)
        config = json.loads(raw) if raw else {}
        if config.get('run_id') != self.config.get('run_id'):
            while self.ready:
                self.discard(self.ready.popleft()[2])
            previous = json.loads(r.get(self.status_key) or '{}')
            self.completed = int(previous.get('completed', 0)) if previous.get('run_id') == config.get('run_id') else 0
            self.active = False
            self.due = 0
            self.error = ''
            self.last_status = None
        elif config.get('command') != self.config.get('command'):
            self.error = ''  # Explicit resume retries a failed preparation.
        self.config = config
        scripts = config.get('scripts', [])
        mode = config.get('mode', 'stopped')
        if not config.get('run_id'):
            return None

        if self.future and self.future[2].done():
            run_id, index, future = self.future
            self.future = None
            try:
                text, clip = future.result()
                if run_id == config['run_id'] and mode != 'stopped':
                    self.ready.append((index, text, clip))
                else:
                    self.discard(clip)
            except Exception:
                logger.exception('[HOST] media preparation failed')
                if run_id == config['run_id']:
                    self.error = '本段素材生成失败，请暂停后继续重试，或结束并修改讲稿。'

        if self.active and pipeline_idle:
            self.active = False
            self.completed += 1
            self.due = time.monotonic()+random.uniform(config['min_pause'], config['max_pause'])
            logger.info('[HOST] run=%s completed=%d/%d', config['run_id'], self.completed, len(scripts))
        if mode == 'stopped':
            while self.ready:
                self.discard(self.ready.popleft()[2])
            self.report(r, 'stopped')
            return None
        if self.completed >= len(scripts):
            self.report(r, 'finished')
            return None
        if mode == 'paused':
            self.report(r, 'paused')
            return None
        if self.error:
            self.report(r, 'failed')
            return None

        # At most two complete paragraphs buffered; active paragraph is separate.
        # Never launch another low-priority model call while manual work is pending.
        index = self.completed + int(self.active) + len(self.ready)
        if not self.future and len(self.ready) < 2 and index < len(scripts) and not manual_busy:
            text = scripts[index]
            def prepare(text=text, index=index, config=config, base_key=base_key):
                clip = renderer.render(text, base_key, work_dir)
                clip.metrics['narration'] = dict(job_id=f"{config['run_id']}-{index}", index=1,
                    count=1, submitted_ms=0, trim_padding=True, host_run_id=config['run_id'])
                return text, clip
            self.future = (config['run_id'], index, self.executor.submit(prepare))

        if manual_busy:
            self.report(r, 'waiting_manual')
        elif self.active:
            self.report(r, 'speaking')
        elif pipeline_idle and self.ready and time.monotonic() >= self.due:
            index, text, clip = self.ready.popleft()
            self.active = True
            self.report(r, 'speaking')
            logger.info('[HOST] run=%s enqueue paragraph=%d', config['run_id'], index+1)
            return text, clip
        else:
            self.report(r, 'waiting' if self.ready else 'preparing')
        return None

    def close(self):
        self.closed = True
        while self.ready:
            self.discard(self.ready.popleft()[2])
        if self.future:
            self.future[2].add_done_callback(lambda future: self.discard(future.result()[1]) if not future.cancelled() and not future.exception() else None)
        self.executor.shutdown(wait=False, cancel_futures=True)
