"""Configured headless room reader. No ports, no posting to Xiaohongshu."""
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import sys
import time
from urllib.parse import urlparse

import redis
import requests
from playwright.async_api import async_playwright

logger = logging.getLogger('xhs_collector')
OBSERVER = (Path(__file__).parent/'streaming/xhs_observer.js').read_text()
ROOM_READY_SELECTOR = '#live-layout #input-area, .msg-content'


def allowed_room_url(value, short=True):
    try:
        url = urlparse(value)
        if url.scheme != 'https' or url.username or url.password or url.port or url.fragment:
            return False
        if url.hostname in ('www.xiaohongshu.com', 'xiaohongshu.com'):
            return bool(re.fullmatch(r'/livestream/\d+/?', url.path))
        return short and url.hostname == 'xhslink.com' and url.path not in ('', '/')
    except ValueError:
        return False


class EventPolicy:
    """Bounded deduplication + independent welcome/reply cooldowns."""
    def __init__(self):
        self.ids = deque(maxlen=1000)
        self.seen = set()
        self.repeats = {}
        self.last = {'enter': 0, 'message': 0}

    def accept(self, event, config, now=None):
        now = now or time.time()
        kind = event.get('kind')
        name = str(event.get('nickname', '')).strip()[:40]
        text = str(event.get('text', '')).strip()[:300]
        event_id = str(event.get('id', ''))[:100]
        observed = event.get('observed_ms', 0)
        if kind not in ('enter', 'message') or not name or not event_id:
            return None
        if not isinstance(observed, (int, float)) or not 0 <= now*1000-observed <= 30000:
            return None
        if event_id in self.seen:
            return None
        if len(self.ids) == self.ids.maxlen:
            self.seen.discard(self.ids[0])
        self.ids.append(event_id); self.seen.add(event_id)
        if kind == 'message' and not text:
            return None
        signature = hashlib.sha256(json.dumps([kind,name,text],ensure_ascii=False).encode()).hexdigest()
        self.repeats = {key:stamp for key,stamp in self.repeats.items() if now-stamp < 300}
        duplicate = now-self.repeats.get(signature, 0) < (300 if kind=='enter' else 15)
        self.repeats[signature] = now
        action = 'display'
        if not duplicate and config.get('welcome' if kind=='enter' else 'reply'):
            if now-self.last[kind] >= (60 if kind=='enter' else 12):
                self.last[kind] = now
                action = 'welcome' if kind=='enter' else 'reply'
        return dict(id=event_id, kind=kind, nickname=name, text=text, observed_ms=observed,
                    action=action, revision=config['revision'])


class Collector:
    def __init__(self, client, api):
        self.redis = client
        self.headless = os.environ.get('XHS_BROWSER_HEADLESS', '0' if sys.platform == 'darwin' else '1').lower() not in ('0','false','no')
        self.api = api.rstrip('/')
        self.tasks = {}
        self.stop = asyncio.Event()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='xhs-actions')
        self.action_slots = asyncio.Semaphore(2)

    def status(self, avatar, config, state, **extra):
        self.redis.set(f'live_xhs_status:{avatar}',json.dumps(dict(state=state,
            revision=config['revision'], updated_ms=int(time.time()*1000), browser_mode='headless' if self.headless else 'visible', **extra),ensure_ascii=False),ex=20)

    async def act(self, avatar, config, event):
        # Do not let slow HTTP actions block DOM polling. Bounded at two jobs.
        async with self.action_slots:
            loop=asyncio.get_running_loop()
            await loop.run_in_executor(self.executor, self._act, avatar, config, event)

    def _act(self, avatar, config, event):
        try:
            current=json.loads(self.redis.get(f'live_xhs_config:{avatar}') or '{}')
            if not current.get('enabled') or current.get('revision') != config['revision']:
                return
            if time.time()*1000-event['observed_ms'] > 30000:
                return
            # Listening works with the avatar off; speaking requires a live session.
            response=requests.get(f'{self.api}/api/live/{avatar}/status',timeout=(3,5))
            if response.status_code != 200: return
            queue_key = f'live_welcome_queue:{avatar}' if event['action']=='welcome' else f'live_external_queue:{avatar}'
            if self.redis.llen(queue_key) >= 3: return
            if event['action']=='welcome':
                name=re.sub(r'[\x00-\x1f<>\[\]{}]', '', event['nickname'])[:24]
                job=dict(text=f'欢迎{name}来到直播间。', narration=True, job_id=f'xhs-{time.time_ns()}',
                         ts=int(time.time()*1000),submitted_ms=int(time.time()*1000), xhs_revision=config['revision'])
                self.redis.eval("if redis.call('LLEN',KEYS[1])>=3 then return 0 end return redis.call('RPUSH',KEYS[1],ARGV[1])",1,
                                queue_key,json.dumps(job,ensure_ascii=False))
            elif event['action']=='reply':
                response=requests.post(f'{self.api}/api/live/{avatar}/message',json=dict(
                    text=f"小红书观众「{event['nickname']}」说：{event['text']}", username=event['nickname'],
                    source='xiaohongshu',roomRevision=config['revision']),timeout=(3,10))
                response.raise_for_status()
        except Exception:
            logger.warning('room action unavailable avatar=%s',avatar) # never signed URL/body

    async def room(self, browser, avatar, config):
        context=None
        actions=set()
        try:
            if not allowed_room_url(config.get('url','')):
                self.status(avatar,config,'error',message='直播间链接无效'); return
            self.status(avatar,config,'connecting')
            if self.headless:
                context=await browser.new_context(locale='zh-CN', viewport={'width':1280,'height':800})
            else:
                profiles=Path(os.environ.get('XHS_BROWSER_PROFILE_DIR', str(Path(__file__).parent.parent/'runtime/xhs-browser')))
                profiles.mkdir(parents=True,exist_ok=True,mode=0o700)
                profile=profiles/f'avatar-{avatar}'
                profile.mkdir(exist_ok=True,mode=0o700)
                context=await self.playwright.chromium.launch_persistent_context(str(profile),
                    headless=False,locale='zh-CN',viewport={'width':1280,'height':800})
            async def route(request_route):
                request=request_route.request
                parsed=urlparse(request.url)
                if self.headless and (request.resource_type=='media' or re.search(r'\.(flv|m3u8|mp4)$',parsed.path,re.I)):
                    await request_route.abort(); return
                if request.is_navigation_request() and request.frame==page.main_frame:
                    if parsed.scheme!='https' or parsed.hostname not in ('www.xiaohongshu.com','xiaohongshu.com','xhslink.com'):
                        await request_route.abort();return
                await request_route.continue_()
            page=context.pages[0] if context.pages else await context.new_page()
            await page.route('**/*',route)
            await page.goto(config['url'],wait_until='domcontentloaded',timeout=45000)
            if not allowed_room_url(page.url,short=False):
                self.status(avatar,config,'blocked',message='短链接未进入直播页，请粘贴浏览器中的完整直播间地址');return
            try:
                await page.wait_for_selector(ROOM_READY_SELECTOR, timeout=30000)
            except Exception:
                body=(await page.locator('body').inner_text())[:5000]
                state='ended' if any(word in body for word in ('直播已结束','直播结束','主播还没开播')) else 'blocked'
                self.status(avatar,config,state,message='未找到弹幕区域；可能需要登录、验证，或直播已结束');return
            # Let the initial history settle, then establish a DOM baseline.
            await page.wait_for_timeout(3000)
            await page.evaluate(OBSERVER)
            policy=EventPolicy();count=0; checked=0
            while not self.stop.is_set():
                if page.is_closed():raise RuntimeError('Room page closed')
                if time.monotonic()-checked > 5:
                    checked=time.monotonic()
                    body=(await page.locator('body').inner_text())[:6000]
                    if any(word in body for word in ('直播已结束','主播还没开播')):
                        self.status(avatar,config,'ended',message='直播已结束');return
                    if any(word in body for word in ('请完成验证','安全验证','拖动滑块')):
                        self.status(avatar,config,'blocked',message='直播页需要人工验证，请检查浏览器登录状态');return
                # A reload creates another initial history; initialize, skip it again.
                installed=await page.evaluate('() => !!window.__lingcastXHS')
                if not installed:
                    await page.wait_for_timeout(3000);await page.evaluate(OBSERVER)
                incoming=await page.evaluate('() => window.__lingcastXHS.events.splice(0,200)')
                for value in incoming:
                    event=policy.accept(value,config)
                    if not event:continue
                    count+=1
                    key=f'live_xhs_events:{avatar}'
                    with self.redis.pipeline() as pipe:
                        pipe.lpush(key,json.dumps(event,ensure_ascii=False));pipe.ltrim(key,0,99);pipe.expire(key,86400);pipe.execute()
                    if event['action']!='display' and len(actions)<2:
                        action=asyncio.create_task(self.act(avatar,config,event));actions.add(action);action.add_done_callback(actions.discard)
                self.status(avatar,config,'listening',received=count)
                await asyncio.sleep(.5)
        except asyncio.CancelledError:raise
        except Exception as error:
            logger.warning('room reader failed avatar=%s type=%s',avatar,type(error).__name__)
            self.status(avatar,config,'error',message='浏览器连接异常，请重新开始监听')
        finally:
            for action in actions:action.cancel()
            if context:await context.close()

    async def run(self):
        async with async_playwright() as playwright:
            self.playwright=playwright
            browser=await playwright.chromium.launch(headless=True) if self.headless else None
            try:
                while not self.stop.is_set():
                    configs={}
                    for key in self.redis.scan_iter('live_xhs_config:*'):
                        avatar=int(key.rsplit(':',1)[1]);configs[avatar]=json.loads(self.redis.get(key) or '{}')
                    for avatar,(revision,task) in list(self.tasks.items()):
                        config=configs.get(avatar,{})
                        if not config.get('enabled') or config.get('client') == 'extension' or config.get('revision')!=revision:
                            task.cancel();await asyncio.gather(task,return_exceptions=True);self.tasks.pop(avatar)
                    for avatar,config in configs.items():
                        if config.get('client') == 'extension':
                            continue
                        if not config.get('enabled'):
                            self.status(avatar,config,'stopped');continue
                        if avatar not in self.tasks:
                            if len(self.tasks)>=4:
                                self.status(avatar,config,'error',message='最多同时监听4个房间');continue
                            self.tasks[avatar]=(config['revision'],asyncio.create_task(self.room(browser,avatar,config)))
                        elif self.tasks[avatar][1].done():
                            # Fail closed. Keep error visible until an explicit restart.
                            value=json.loads(self.redis.get(f'live_xhs_status:{avatar}') or '{}')
                            self.status(avatar,config,value.get('state','error'),message=value.get('message','请重新开始监听'))
                        else:
                            # Navigation and history settling may take longer than a status TTL.
                            value=json.loads(self.redis.get(f'live_xhs_status:{avatar}') or '{}')
                            self.status(avatar,config,value.get('state','connecting'),
                                        **{k:v for k,v in value.items() if k not in ('state','revision','updated_ms','browser_mode')})
                    try:await asyncio.wait_for(self.stop.wait(),timeout=1)
                    except asyncio.TimeoutError:pass
            finally:
                for _,task in self.tasks.values():task.cancel()
                await asyncio.gather(*(task for _,task in self.tasks.values()),return_exceptions=True)
                if browser:await browser.close()
                self.executor.shutdown(wait=False,cancel_futures=True)


async def main():
    from worker import _load_local_env, load_config
    _load_local_env();config=load_config()
    client=redis.Redis(host=config['redis_host'],port=config['redis_port'],password=config['redis_password'] or None,
                       db=config['redis_db'],decode_responses=True)
    collector=Collector(client,config['api_base_url'])
    loop=asyncio.get_running_loop()
    for signum in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(signum,collector.stop.set)
    await collector.run()


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    asyncio.run(main())
