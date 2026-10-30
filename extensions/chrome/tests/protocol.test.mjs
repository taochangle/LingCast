import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
// Source is copied into a data: module so the extension does not need package.json.
const source=fs.readFileSync(new URL('../protocol.js',import.meta.url),'utf8');
const {SERVER_ORIGIN,serverOrigin,roomURL,normalizeEvents}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('default server comes from local packaging and live-room URLs are validated',()=>{
 assert.equal(SERVER_ORIGIN,'');
 assert.equal(serverOrigin('https://collector.example.invalid:8443/'),'https://collector.example.invalid:8443');
 for(const url of ['file:///tmp/a','https://user:password@example.invalid','https://example.invalid/path','https://example.invalid?key=private','https://example.invalid/#fragment']) assert.throws(()=>serverOrigin(url));
 assert.equal(roomURL('https://xiaohongshu.com/livestream/123?xsec_token=private'),'https://www.xiaohongshu.com/livestream/123');
 for(const url of ['https://xhslink.com/o/123','https://www.xiaohongshu.com.evil.test/livestream/123','https://www.xiaohongshu.com/explore/123','http://www.xiaohongshu.com/livestream/123']) assert.throws(()=>roomURL(url));
});
test('bounded normalized messages, clock offset, stale messages discarded',()=>{
 const event={id:'new-1',kind:'message',nickname:' 小雨 ',text:'你好😀',observed_ms:10000};
 assert.deepEqual(normalizeEvents([event],500,11000),[{...event,nickname:'小雨',observed_ms:10500}]);
 assert.deepEqual(normalizeEvents([event],0,41000),[]);
 assert.deepEqual(normalizeEvents([{...event,kind:'gift'}],0,11000),[]);
 assert.throws(()=>normalizeEvents(new Array(21).fill(event),0,11000));
 assert.equal([...normalizeEvents([{...event,nickname:'雨'.repeat(60),text:'😀'.repeat(400)}],0,11000)[0].text].length,300);
});
test('manifest limits reading to user action, no cookie/network interception permission',()=>{
 const manifest=JSON.parse(fs.readFileSync(new URL('../manifest.json',import.meta.url)));
 assert.deepEqual(manifest.permissions,['activeTab','scripting','storage','alarms']);
 assert.equal(manifest.content_scripts,undefined);
 assert.equal(manifest.host_permissions,undefined);
 assert.equal(manifest.externally_connectable,undefined);
 assert.deepEqual(manifest.optional_host_permissions,['http://*/*','https://*/*']);
 const popup=fs.readFileSync(new URL('../popup.html',import.meta.url),'utf8');
 const input=popup.match(/<input id="server"[^>]*>/)[0];
 assert.doesNotMatch(input,/readonly/);
 assert.match(input,/value=""/);
});

test('dynamic share paths identify the same room without forwarding share tokens',()=>{
 assert.equal(roomURL('https://www.xiaohongshu.com/livestream/dynpath4EqZtflx/570517962193993784?xsec_token=private'), 'https://www.xiaohongshu.com/livestream/570517962193993784');
 for (const path of ['/livestream/dynpath4EqZtflx/123/extra','/livestream/other/123','/livestream/dynpath4EqZtflx/not-a-number']) assert.throws(()=>roomURL('https://www.xiaohongshu.com'+path));
});

test('like and gift notices are normalized, malformed gifts rejected',()=>{
 const base={id:'like-1',nickname:'Lynn',text:'',observed_ms:10000};
 assert.equal(normalizeEvents([{...base,kind:'like'}],0,11000)[0].kind,'like');
 assert.equal(normalizeEvents([{...base,kind:'gift',text:'人气票 x1'}],0,11000)[0].text,'人气票 x1');
 assert.equal(normalizeEvents([{...base,kind:'gift',text:'人气票 x0'}],0,11000).length,0);
});
