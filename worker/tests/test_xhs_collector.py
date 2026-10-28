import unittest
from xhs_collector import EventPolicy, allowed_room_url


class XHSCollectorTests(unittest.TestCase):
    def test_allowed_links(self):
        self.assertTrue(allowed_room_url('https://www.xiaohongshu.com/livestream/123?xsec_token=abc'))
        self.assertTrue(allowed_room_url('https://xhslink.com/o/example'))
        for value in ('http://www.xiaohongshu.com/livestream/1','https://www.xiaohongshu.com.evil.test/livestream/1',
                      'https://user@www.xiaohongshu.com/livestream/1','https://127.0.0.1:59480',
                      'https://www.xiaohongshu.com:8443/livestream/1','https://www.xiaohongshu.com/explore/123'):
            self.assertFalse(allowed_room_url(value),value)
        self.assertFalse(allowed_room_url('https://xhslink.com/o/example',short=False))

    def test_dedup_expiration_and_rate_limits(self):
        policy=EventPolicy();config=dict(revision=1,welcome=True,reply=True)
        def event(number,kind='message',text='你好',name='小雨',stamp=1000000):
            return dict(id=str(number),kind=kind,nickname=name,text=text,observed_ms=stamp)
        first=policy.accept(event(1),config,1000)
        self.assertEqual(first['action'],'reply')
        self.assertIsNone(policy.accept(event(1),config,1000))
        self.assertEqual(policy.accept(event(2),config,1001)['action'],'display')
        self.assertEqual(policy.accept(event(3,text='项目怎么启动'),config,1002)['action'],'display')
        self.assertEqual(policy.accept(event(4,text='项目架构',stamp=1013000),config,1013)['action'],'reply')
        self.assertEqual(policy.accept(event(5,kind='enter'),config,1000)['action'],'welcome')
        self.assertEqual(policy.accept(event(6,kind='enter',name='小明',stamp=1030000),config,1030)['action'],'display')
        self.assertEqual(policy.accept(event(7,kind='enter',name='小明',stamp=1061000),config,1061)['action'],'display')
        self.assertIsNone(policy.accept(event(8),config,1100))
        self.assertIsNone(policy.accept(event(9,stamp=1200000),config,1100))

    def test_listen_only_never_speaks(self):
        event=dict(id='1',kind='message',nickname='小雨',text='你好',observed_ms=1000000)
        self.assertEqual(EventPolicy().accept(event,dict(revision=1),1000)['action'],'display')

class XHSObserverDOMTests(unittest.TestCase):
    def test_history_host_likes_and_emoji(self):
        from playwright.sync_api import sync_playwright
        from xhs_collector import OBSERVER
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.set_content('<div class="msg-wrapper role-user"><span class="nickname">历史</span><span class="content">不要补播</span></div>')
                # crypto.randomUUID is secure-context only; fixtures use a local origin.
                page.route('https://collector.test/**',lambda route:route.fulfill(body='<div class="msg-wrapper role-user"><span class="nickname">历史</span><span class="content">不要补播</span></div>',content_type='text/html'))
                page.goto('https://collector.test/')
                page.evaluate(OBSERVER)
                self.assertEqual(page.evaluate('window.__lingcastXHS.events'),[])
                page.evaluate('''() => {
                  document.body.insertAdjacentHTML('beforeend', `
                    <div class="msg-wrapper role-user"><span class="nickname">小雨</span><span class="content">你好<img alt="[微笑]" /></span></div>
                    <div class="msg-wrapper role-host"><span class="nickname">主播</span><span class="content">忽略主播</span></div>
                    <div class="msg-wrapper role-user"><span class="nickname">点赞</span><span> 为主播点赞了</span></div>
                    <div class="enter-msgs"><div class="msg-wrapper role-user"><span class="nickname">小明</span><span> 来了</span></div></div>`);
                }''')
                page.wait_for_function('window.__lingcastXHS.events.length === 2')
                events=page.evaluate('window.__lingcastXHS.events')
                self.assertEqual([(e['kind'],e['nickname'],e['text']) for e in events],
                                 [('message','小雨','你好[微笑]'),('enter','小明','')])
            finally:browser.close()

class ExternalQueuePriorityTests(unittest.TestCase):
    def test_manual_before_external_and_old_room_replies_dropped(self):
        import json
        from unittest.mock import Mock
        from streaming.incoming import pop_live_job
        lists={'live_queue:1':['manual'],'live_external_queue:1':[json.dumps({'xhs_revision':1}),json.dumps({'xhs_revision':2})]}
        client=Mock()
        client.lpop.side_effect=lambda key:lists[key].pop(0) if lists[key] else None
        client.get.return_value=json.dumps(dict(enabled=True,revision=2))
        self.assertEqual(pop_live_job(client,'live_queue:1',1),'manual')
        self.assertEqual(len(lists['live_external_queue:1']),2)
        self.assertIsNone(pop_live_job(client,'live_queue:1',1))
        self.assertEqual(json.loads(pop_live_job(client,'live_queue:1',1))['xhs_revision'],2)
