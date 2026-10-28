"""Bounded, lower-priority autonomous presenter; Redis owns the session plan."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import random
import time
import copy
import shutil
import uuid

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
        self.library = {}
        self.library_order = deque()
        self.library_base = None

    @staticmethod
    def discard(clip):
        clip.path.unlink(missing_ok=True)

    def report(self, r, phase):
        status = dict(run_id=self.config.get('run_id'), phase=phase, completed=self.completed,
                      total=len(self.config.get('scripts', [])), prepared=len(self.library) if self.config.get("kind") == "library" else len(self.ready),
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
            self.clear_library()
            self.library_base = base_key
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
        library = config.get('kind') == 'library'
        if not config.get('run_id'):
            return None

        if self.future and self.future[2].done():
            run_id, index, future = self.future
            self.future = None
            try:
                text, clip = future.result()
                if run_id == config['run_id'] and mode != 'stopped':
                    if library:
                        self.library[index] = (text, clip)
                    else:
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
            self.clear_library()
            self.report(r, 'stopped')
            return None
        if not library and self.completed >= len(scripts):
            self.report(r, 'finished')
            return None
        if mode == 'paused':
            self.report(r, 'paused')
            return None
        if library and self.library_base != base_key:
            self.error = "待机底片已改变，请结束并重新准备话术库。"
        if self.error:
            self.report(r, 'failed')
            return None

        # At most two complete paragraphs buffered; active paragraph is separate.
        # Never launch another low-priority model call while manual work is pending.
        index = (next((i for i in range(len(scripts)) if i not in self.library), len(scripts))
                 if library else self.completed + int(self.active) + len(self.ready))
        if not self.future and (library or len(self.ready) < 2) and index < len(scripts) and not manual_busy:
            text = scripts[index]
            def prepare(text=text, index=index, config=config, base_key=base_key):
                clip = renderer.render(text, base_key, work_dir)
                clip.metrics['narration'] = dict(job_id=f"{config['run_id']}-{index}", index=1,
                    count=1, submitted_ms=0, trim_padding=True, host_run_id=config['run_id'])
                return text, clip
            self.future = (config['run_id'], index, self.executor.submit(prepare))

        if library and len(self.library) == len(scripts) and not self.active and pipeline_idle and not manual_busy and time.monotonic() >= self.due:
            if not self.library_order:
                welcome_count = config.get('welcome_count', len(scripts)//2)
                welcomes = list(range(welcome_count))
                others = list(range(welcome_count, len(scripts)))
                random.shuffle(welcomes)
                random.shuffle(others)
                for first, second in zip(welcomes, others):
                    self.library_order.extend([first, second])
                self.library_order.extend(welcomes[len(others):])
                self.library_order.extend(others[len(welcomes):])
            index = self.library_order.popleft()
            text, source = self.library[index]
            clip = copy.deepcopy(source)
            clip.path = source.path.with_name('host-play-'+uuid.uuid4().hex+'.mp4')
            shutil.copyfile(source.path, clip.path)
            self.active = True
            self.report(r, 'speaking')
            return text, clip

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
            self.report(r, 'waiting' if self.ready or (library and len(self.library) == len(scripts)) else 'preparing')
        return None

    def clear_library(self):
        for _, clip in self.library.values():
            self.discard(clip)
        self.library.clear()
        self.library_order.clear()

    def close(self):
        self.closed = True
        self.clear_library()
        while self.ready:
            self.discard(self.ready.popleft()[2])
        if self.future:
            self.future[2].add_done_callback(lambda future: self.discard(future.result()[1]) if not future.cancelled() and not future.exception() else None)
        self.executor.shutdown(wait=False, cancel_futures=True)
