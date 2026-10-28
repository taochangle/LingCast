"""DOM fixtures only: never opens a real livestream or user's Chrome profile."""
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright

class ChromeCollectorDOMTests(unittest.TestCase):
    def test_baseline_virtual_nodes_emoji_enter_and_stop(self):
        script=(Path(__file__).resolve().parents[2]/'extensions/chrome/dom-reader.js').read_text()
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('https://collector.test/**',lambda route:route.fulfill(content_type='text/html',body='''
                    <div id="live-layout"><div id="input-area">聊天</div></div>
                    <div id="reused" class="msg-wrapper role-user"><span class="nickname">历史</span><span class="content">旧消息</span></div>'''))
                page.goto('https://collector.test/')
                page.evaluate(script)
                page.evaluate('LingCastDOM.start()')
                self.assertEqual(page.evaluate('LingCastDOM.drain()'),[])
                page.wait_for_timeout(2100)
                page.evaluate('''() => document.body.insertAdjacentHTML('beforeend', `
                    <div class="msg-wrapper role-user"><span class="nickname">小雨</span><span class="content">你好<img alt="[微笑]" /></span></div>
                    <div class="msg-wrapper role-host"><span class="nickname">主播</span><span class="content">忽略</span></div>
                    <div class="msg-wrapper role-user"><span class="nickname">点赞通知</span><span>点赞了</span></div>
                    <div class="enter-msgs"><div class="msg-content"><span class="nickname">小明</span> 来了</div></div>`)''')
                page.wait_for_timeout(250)
                events=page.evaluate('LingCastDOM.drain()')
                self.assertEqual([(e['kind'],e['nickname'],e['text']) for e in events],[('message','小雨','你好[微笑]'),('enter','小明','')])
                page.evaluate("document.querySelector('#reused .content').textContent='复用节点的新消息'")
                page.wait_for_timeout(250)
                self.assertEqual(page.evaluate('LingCastDOM.drain()[0].text'),'复用节点的新消息')
                page.evaluate('LingCastDOM.stop()')
                page.evaluate("document.querySelector('#reused .content').textContent='停止后不应读取'")
                page.wait_for_timeout(250)
                self.assertEqual(page.evaluate('LingCastDOM.drain()'),[])
            finally: browser.close()
